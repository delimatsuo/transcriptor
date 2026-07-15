"""Conformance tests for the deterministic offline protocol simulator."""

import json
import unittest

from tars_phase1a.fixtures import load_committed_catalog
from tars_phase1a.model import (
    AudioChunk,
    ProtocolViolation,
    Source,
    StreamKey,
    TerminalKind,
    UnknownEndCoverage,
)
from tars_phase1a.simulator import (
    AuthorizationRejected,
    CrashPoint,
    FakePrincipal,
    OfflineProtocolSimulator,
    PrincipalState,
    StageWatermark,
    WatermarkSet,
)


class SimulatorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = FakePrincipal("org_fixture", "user_fixture")
        self.key = StreamKey("session_fixture", "stream_mic", 0, Source.MICROPHONE)
        self.payload = load_committed_catalog().generate("counter-3200-v1")
        self.simulator = OfflineProtocolSimulator("session_fixture", self.owner)

    def chunk(
        self,
        sequence: int,
        key: StreamKey = None,  # type: ignore[assignment]
        payload: bytes = None,  # type: ignore[assignment]
        device_id: str = "device_phase1a",
    ) -> AudioChunk:
        selected_key = self.key if key is None else key
        selected_payload = self.payload if payload is None else payload
        first_sample = sequence * 1600
        return AudioChunk(
            key=selected_key,
            sequence=sequence,
            first_sample=first_sample,
            last_sample_exclusive=first_sample + 1600,
            captured_at_monotonic_ns=sequence * 100_000_000,
            captured_at_wall_clock="2026-07-15T20:00:00Z",
            sample_rate_hertz=16_000,
            channel_count=1,
            duration_ms=100,
            payload=selected_payload,
            device_id=device_id,
        )


class IdentityAndFenceTests(SimulatorTestCase):
    def test_fake_identity_failures_are_non_enumerating(self) -> None:
        rejected = (
            FakePrincipal("org_fixture", "user_fixture", PrincipalState.UNAUTHENTICATED),
            FakePrincipal("org_fixture", "user_fixture", PrincipalState.EXPIRED),
            FakePrincipal("org_fixture", "user_fixture", PrincipalState.REVOKED),
            FakePrincipal("org_other", "user_fixture"),
            FakePrincipal("org_fixture", "user_other"),
        )
        messages = []
        for principal in rejected:
            with self.subTest(principal=principal):
                with self.assertRaises(AuthorizationRejected) as context:
                    self.simulator.capture(principal, self.chunk(0))
                messages.append(str(context.exception))
        self.assertEqual(set(messages), {"message rejected"})
        self.assertEqual(self.simulator.diagnostics()["streamCount"], 0)

    def test_capture_lease_renewal_fences_old_device_and_generation(self) -> None:
        self.assertEqual(
            self.simulator.renew_capture_lease(self.owner, "device_generation_1"),
            1,
        )
        with self.assertRaises(ProtocolViolation):
            self.simulator.capture(self.owner, self.chunk(0))

        new_key = StreamKey(
            "session_fixture", "stream_mic_generation_1", 1, Source.MICROPHONE
        )
        self.assertTrue(
            self.simulator.capture(
                self.owner,
                self.chunk(0, key=new_key, device_id="device_generation_1"),
            )
        )

    def test_lease_cannot_rotate_with_unresolved_raw_audio(self) -> None:
        self.simulator.capture(self.owner, self.chunk(0))
        with self.assertRaises(ProtocolViolation):
            self.simulator.renew_capture_lease(self.owner, "device_generation_1")

    def test_lease_cannot_rotate_with_unresolved_forwarding(self) -> None:
        self.simulator.submit(self.owner, self.chunk(0))
        self.simulator.provider_write(self.owner, self.key)
        self.simulator.journal_provider_write(self.owner, self.key)
        with self.assertRaises(ProtocolViolation):
            self.simulator.renew_capture_lease(self.owner, "device_generation_1")

    def test_stale_stt_attempt_is_rejected(self) -> None:
        self.simulator.submit(self.owner, self.chunk(0))
        self.assertEqual(self.simulator.rotate_stt_attempt(self.owner, self.key), 1)
        with self.assertRaises(ProtocolViolation):
            self.simulator.provider_write(
                self.owner, self.key, attempt_generation=0
            )
        coverage = self.simulator.provider_write(
            self.owner, self.key, attempt_generation=1
        )
        self.assertEqual(coverage.coverage_id, self.chunk(0).coverage.coverage_id)


class WatermarkAndRetryTests(SimulatorTestCase):
    def test_admission_does_not_release_audio_but_journaled_forwarding_does(self) -> None:
        self.assertTrue(self.simulator.submit(self.owner, self.chunk(0)))
        admitted = self.simulator.watermarks(self.key)
        self.assertEqual(admitted.admitted, StageWatermark(0, 1600, 0))
        self.assertEqual(admitted.forwarded, StageWatermark())
        self.assertGreater(self.simulator.raw_payload_bytes(self.key), 0)

        coverage = self.simulator.provider_write(self.owner, self.key)
        self.assertGreater(self.simulator.raw_payload_bytes(self.key), 0)
        record = self.simulator.journal_provider_write(self.owner, self.key)
        self.assertEqual(record.coverage, coverage)
        self.assertEqual(record.first_captured_at_monotonic_ns, 0)
        self.assertEqual(record.last_captured_at_monotonic_ns, 0)
        self.assertEqual(self.simulator.raw_payload_bytes(self.key), 0)
        self.assertEqual(
            self.simulator.watermarks(self.key).forwarded,
            StageWatermark(0, 1600, 0),
        )
        self.assertEqual(
            self.simulator.watermarks(self.key).durable_transcript,
            StageWatermark(),
        )

        outcome, committed = self.simulator.commit_transcript(
            self.owner, self.key, coverage
        )
        self.assertTrue(committed)
        self.assertEqual(outcome.kind, TerminalKind.TRANSCRIPT)
        self.assertEqual(
            self.simulator.watermarks(self.key).durable_transcript,
            StageWatermark(0, 1600, 0),
        )

    def test_capture_retry_is_idempotent_and_changed_payload_fails(self) -> None:
        chunk = self.chunk(0)
        self.assertTrue(self.simulator.capture(self.owner, chunk))
        self.assertFalse(self.simulator.capture(self.owner, chunk))

        changed_payload = load_committed_catalog().generate("lcg-3200-v1")
        with self.assertRaises(ProtocolViolation):
            self.simulator.capture(
                self.owner, self.chunk(0, payload=changed_payload)
            )

    def test_rejected_out_of_order_capture_does_not_poison_retry_ledger(self) -> None:
        with self.assertRaises(ProtocolViolation):
            self.simulator.capture(self.owner, self.chunk(1))
        self.assertTrue(self.simulator.capture(self.owner, self.chunk(0)))

    def test_sources_have_independent_order_and_watermarks(self) -> None:
        system_key = StreamKey(
            "session_fixture", "stream_system", 0, Source.SYSTEM_AUDIO
        )
        self.simulator.submit(self.owner, self.chunk(0))
        self.simulator.submit(self.owner, self.chunk(0, key=system_key))
        self.assertEqual(
            self.simulator.watermarks(self.key).admitted,
            StageWatermark(0, 1600, 0),
        )
        self.assertEqual(
            self.simulator.watermarks(system_key).admitted,
            StageWatermark(0, 1600, 0),
        )
        self.simulator.provider_write(self.owner, system_key)
        self.simulator.journal_provider_write(self.owner, system_key)
        self.assertEqual(
            self.simulator.watermarks(self.key).forwarded, StageWatermark()
        )
        self.assertEqual(
            self.simulator.watermarks(system_key).forwarded,
            StageWatermark(0, 1600, 0),
        )

    def test_terminal_replay_is_idempotent_after_attempt_rotation(self) -> None:
        self.simulator.submit(self.owner, self.chunk(0))
        coverage = self.simulator.provider_write(self.owner, self.key)
        self.simulator.journal_provider_write(self.owner, self.key)
        first, committed = self.simulator.commit_transcript(
            self.owner, self.key, coverage
        )
        self.assertTrue(committed)
        self.simulator.rotate_stt_attempt(self.owner, self.key)
        replay, committed = self.simulator.commit_transcript(
            self.owner, self.key, coverage
        )
        self.assertFalse(committed)
        self.assertEqual(replay.event_id, first.event_id)
        self.assertEqual(replay.stt_attempt_generation, 0)
        self.assertEqual(len(self.simulator.terminal_outcomes(self.key)), 1)

    def test_gateway_capacity_rejects_without_releasing_client_audio(self) -> None:
        simulator = OfflineProtocolSimulator(
            "session_fixture",
            self.owner,
            max_client_buffer_bytes=6400,
            max_gateway_queue_bytes=3200,
        )
        simulator.capture(self.owner, self.chunk(0))
        simulator.capture(self.owner, self.chunk(1))
        simulator.admit(self.owner, self.key, 0)
        with self.assertRaises(ProtocolViolation):
            simulator.admit(self.owner, self.key, 1)
        self.assertEqual(
            simulator.watermarks(self.key).admitted, StageWatermark(0, 1600, 0)
        )
        self.assertGreater(simulator.raw_payload_bytes(self.key), 0)


class ReconnectAndCrashTests(SimulatorTestCase):
    def test_crash_before_provider_write_returns_exact_resend_range(self) -> None:
        self.simulator.submit(self.owner, self.chunk(0))
        self.simulator.submit(self.owner, self.chunk(1))
        self.assertEqual(
            self.simulator.recover(
                self.owner, self.key, CrashPoint.BEFORE_PROVIDER_WRITE
            ),
            (),
        )
        plan = self.simulator.reconnect(self.owner, self.key)
        self.assertEqual(plan.authoritative.admitted, StageWatermark())
        self.assertEqual(plan.authoritative.forwarded, StageWatermark())
        self.assertEqual(len(plan.resend_ranges), 1)
        resend = plan.resend_ranges[0]
        self.assertEqual(
            (
                resend.first_sequence,
                resend.last_sequence_inclusive,
                resend.first_sample,
                resend.last_sample_exclusive,
            ),
            (0, 1, 0, 3200),
        )

        self.simulator.admit(self.owner, self.key, 0)
        self.simulator.admit(self.owner, self.key, 1)
        forwarded = self.simulator.provider_write(self.owner, self.key, count=2)
        self.assertEqual(forwarded.coverage_id, resend.coverage_id)

    def test_reconnect_rejects_client_watermark_ahead_of_authority(self) -> None:
        with self.assertRaises(ProtocolViolation):
            self.simulator.reconnect(
                self.owner,
                self.key,
                WatermarkSet(admitted=StageWatermark(0, 1600, 0)),
            )

    def test_reconnect_rejects_inconsistent_sample_or_time_range(self) -> None:
        self.simulator.submit(self.owner, self.chunk(0))
        for inconsistent in (
            StageWatermark(0, 1599, 0),
            StageWatermark(0, 1600, 1),
        ):
            with self.subTest(inconsistent=inconsistent):
                with self.assertRaises(ProtocolViolation):
                    self.simulator.reconnect(
                        self.owner,
                        self.key,
                        WatermarkSet(admitted=inconsistent),
                    )

    def test_crash_after_unjournaled_write_creates_exact_ambiguous_gap(self) -> None:
        self.simulator.submit(self.owner, self.chunk(0))
        coverage = self.simulator.provider_write(self.owner, self.key)
        gaps = self.simulator.recover(
            self.owner,
            self.key,
            CrashPoint.AFTER_PROVIDER_WRITE_BEFORE_JOURNAL,
        )
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].coverage, coverage)
        self.assertEqual(gaps[0].reason_code, "unknown_forwarding_state")
        self.assertEqual(self.simulator.watermarks(self.key).forwarded, StageWatermark())
        self.assertEqual(self.simulator.raw_payload_bytes(self.key), 0)
        self.assertEqual(self.simulator.reconnect(self.owner, self.key).resend_ranges, ())

    def test_crash_after_journal_creates_gap_without_regressing_forwarding(self) -> None:
        self.simulator.submit(self.owner, self.chunk(0))
        coverage = self.simulator.provider_write(self.owner, self.key)
        self.simulator.journal_provider_write(self.owner, self.key)
        gaps = self.simulator.recover(
            self.owner,
            self.key,
            CrashPoint.AFTER_JOURNAL_BEFORE_TRANSCRIPT,
        )
        self.assertEqual(gaps[0].coverage, coverage)
        self.assertEqual(gaps[0].reason_code, "stt_stream_failed")
        self.assertEqual(
            self.simulator.watermarks(self.key).forwarded,
            StageWatermark(0, 1600, 0),
        )
        self.assertEqual(
            self.simulator.watermarks(self.key).durable_transcript,
            StageWatermark(),
        )

    def test_crash_after_terminal_commit_replays_same_terminal_once(self) -> None:
        self.simulator.submit(self.owner, self.chunk(0))
        coverage = self.simulator.provider_write(self.owner, self.key)
        self.simulator.journal_provider_write(self.owner, self.key)
        terminal, _ = self.simulator.commit_transcript(
            self.owner, self.key, coverage
        )
        replay = self.simulator.recover(
            self.owner, self.key, CrashPoint.AFTER_TERMINAL_COMMIT
        )
        self.assertEqual(replay[0].event_id, terminal.event_id)
        self.assertEqual(len(self.simulator.terminal_outcomes(self.key)), 1)
        self.assertEqual(self.simulator.reconnect(self.owner, self.key).resend_ranges, ())

    def test_forced_termination_records_honest_unknown_end_coverage(self) -> None:
        self.simulator.capture(self.owner, self.chunk(0))
        gap = self.simulator.record_unknown_end_termination(
            self.owner,
            self.key,
            first_sequence=0,
            first_sample=0,
        )
        self.assertIsInstance(gap.coverage, UnknownEndCoverage)
        self.assertEqual(gap.reason_code, "process_terminated")
        self.assertEqual(self.simulator.raw_payload_bytes(self.key), 0)
        self.assertEqual(self.simulator.reconnect(self.owner, self.key).resend_ranges, ())


class BoundsAndDiagnosticsTests(SimulatorTestCase):
    def test_client_buffer_overflow_creates_exact_terminal_gap(self) -> None:
        simulator = OfflineProtocolSimulator(
            "session_fixture",
            self.owner,
            max_client_buffer_bytes=3200,
        )
        self.assertTrue(simulator.capture(self.owner, self.chunk(0)))
        self.assertFalse(simulator.capture(self.owner, self.chunk(1)))
        outcomes = simulator.terminal_outcomes(self.key)
        self.assertEqual(len(outcomes), 1)
        gap = outcomes[0]
        self.assertEqual(gap.kind, TerminalKind.GAP)
        self.assertEqual(gap.reason_code, "buffer_overflow")
        self.assertEqual(
            (
                gap.coverage.first_sequence,
                gap.coverage.last_sequence_inclusive,
                gap.coverage.first_sample,
                gap.coverage.last_sample_exclusive,
            ),
            (1, 1, 1600, 3200),
        )
        with self.assertRaises(ProtocolViolation):
            simulator.capture(self.owner, self.chunk(2))

    def test_diagnostics_and_forwarding_journal_are_content_free(self) -> None:
        self.simulator.submit(self.owner, self.chunk(0))
        self.simulator.provider_write(self.owner, self.key)
        record = self.simulator.journal_provider_write(self.owner, self.key)
        rendered = json.dumps(
            {
                "diagnostics": self.simulator.diagnostics(),
                "journal": record.to_dict(),
            },
            sort_keys=True,
        )
        fixture_digest = (
            "78ad7b2c3cf464e4e219f6044605741a65a8197287a6951d142870af42c3397d"
        )
        self.assertNotIn(fixture_digest, rendered)
        for forbidden in (
            "payloadDigestSha256",
            "transcriptText",
            "noteText",
            "credential",
            "participantName",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()

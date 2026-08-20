"""Conformance tests for canonical Python protocol bindings."""

import hashlib
import json
import unittest
from pathlib import Path

from tars_phase1a.fixtures import load_committed_catalog
from tars_phase1a.model import (
    MAX_AUDIO_PAYLOAD_BYTES,
    AudioChunk,
    EventLedger,
    KnownCoverage,
    ProtocolViolation,
    Source,
    StreamKey,
    TerminalKind,
    TerminalLedger,
    TerminalOutcome,
    UnknownEndCoverage,
    deterministic_event_id,
)


PROTOCOL_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROTOCOL_ROOT / "schema" / "protocol-v1.schema.json"


class CanonicalIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = StreamKey("session_test", "stream_mic", 0, Source.MICROPHONE)

    def test_known_coverage_id_matches_independent_vector(self) -> None:
        coverage = KnownCoverage(self.key, 0, 1, 0, 1600)
        self.assertEqual(
            coverage.coverage_id,
            "cov_a992886d0ee5d24f56ef8688aff178b7103a9810f24326e6c98301e74405708d",
        )

    def test_audio_event_id_matches_independent_vector(self) -> None:
        self.assertEqual(
            deterministic_event_id("audio.chunk", self.key, "0"),
            "evt_9a728a2e6bcaa94c0c0da221164f1f9e84cf4fc5a37da4dfd1b9ca3d85ccd2d9",
        )

    def test_identifiers_reject_nul_and_out_of_contract_characters(self) -> None:
        for session_id in ("bad\0id", "bad id", "", "a" * 129):
            with self.subTest(session_id=session_id):
                with self.assertRaises(ProtocolViolation):
                    StreamKey(session_id, "stream_mic", 0, Source.MICROPHONE)

    def test_attempt_generation_does_not_change_terminal_id(self) -> None:
        coverage = KnownCoverage(self.key, 0, 1, 0, 1600)
        first = TerminalOutcome(TerminalKind.TRANSCRIPT, coverage, 1)
        second = TerminalOutcome(TerminalKind.TRANSCRIPT, coverage, 9)
        self.assertEqual(
            first.event_id,
            "term_0c1b165c6cf18b274a0da386c344d9848e7b48922be76075bea67800c02c1949",
        )
        self.assertEqual(first.event_id, second.event_id)

    def test_terminal_binding_contains_every_schema_required_field(self) -> None:
        coverage = KnownCoverage(self.key, 0, 1, 0, 1600)
        outcome = TerminalOutcome(TerminalKind.TRANSCRIPT, coverage, 1)
        with SCHEMA_PATH.open("r", encoding="utf-8") as schema_file:
            schema = json.load(schema_file)
        required = set(schema["$defs"]["terminalOutcome"]["allOf"][1]["required"])
        metadata = json.loads(outcome.encoded_metadata().decode("utf-8"))
        self.assertTrue(required.issubset(metadata))
        allowed = set(schema["$defs"]["terminalOutcome"]["allOf"][1]["properties"])
        self.assertTrue(set(metadata).issubset(allowed))
        self.assertEqual(metadata["source"], "microphone")
        self.assertEqual(metadata["resultOrdinal"], 0)
        self.assertNotIn("transcriptText", metadata)

    def test_protocol_integer_fields_reject_boolean_and_float_values(self) -> None:
        for generation in (True, 1.5):
            with self.subTest(generation=generation):
                with self.assertRaises(ProtocolViolation):
                    StreamKey("session_test", "stream_mic", generation, Source.MICROPHONE)

        for first_sequence in (True, 1.5):
            with self.subTest(first_sequence=first_sequence):
                with self.assertRaises(ProtocolViolation):
                    KnownCoverage(self.key, first_sequence, 1, 0, 1600)

    def test_event_identity_and_reason_codes_are_bounded_identifiers(self) -> None:
        with self.assertRaises(ProtocolViolation):
            deterministic_event_id("x" * 65, self.key, "0")
        with self.assertRaises(ProtocolViolation):
            deterministic_event_id("audio.chunk", self.key, "x" * 129)
        with self.assertRaises(ProtocolViolation):
            TerminalOutcome(
                TerminalKind.GAP,
                KnownCoverage(self.key, 0, 0, 0, 800),
                0,
                reason_code="x" * 65,
            )

    def test_unknown_end_gap_id_is_deterministic(self) -> None:
        coverage = UnknownEndCoverage(self.key, 2, 1600)
        gap = TerminalOutcome(
            TerminalKind.GAP,
            coverage,
            stt_attempt_generation=1,
            reason_code="process_terminated",
        )
        self.assertEqual(
            gap.event_id,
            "term_d0bc50a96e57a62f03c29eb2679b95ad95851d54834429a54398f99aa6107c1e",
        )


class ChunkAndRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = StreamKey("session_test", "stream_mic", 0, Source.MICROPHONE)
        self.payload = load_committed_catalog().generate("counter-3200-v1")

    def make_chunk(self, **changes):  # type: ignore[no-untyped-def]
        values = {
            "key": self.key,
            "sequence": 0,
            "first_sample": 0,
            "last_sample_exclusive": 1600,
            "captured_at_monotonic_ns": 1_000_000,
            "captured_at_wall_clock": "2026-07-15T20:00:00Z",
            "sample_rate_hertz": 16_000,
            "channel_count": 1,
            "duration_ms": 100,
            "payload": self.payload,
        }
        values.update(changes)
        return AudioChunk(**values)

    def test_fixture_chunk_has_bounded_content_free_metadata(self) -> None:
        chunk = self.make_chunk()
        metadata = json.loads(chunk.encoded_metadata().decode("utf-8"))
        self.assertEqual(metadata["payloadBytes"], 3200)
        self.assertEqual(
            metadata["payloadDigestSha256"], hashlib.sha256(self.payload).hexdigest()
        )
        self.assertNotIn("payload", metadata)
        self.assertNotIn("audio", metadata)
        self.assertNotIn("transcriptText", metadata)

    def test_retry_is_idempotent_and_payload_change_is_rejected(self) -> None:
        chunk = self.make_chunk()
        ledger = EventLedger()
        self.assertTrue(ledger.observe(chunk.event_id, chunk.payload_digest_sha256))
        self.assertFalse(ledger.observe(chunk.event_id, chunk.payload_digest_sha256))
        with self.assertRaises(ProtocolViolation):
            ledger.observe(chunk.event_id, "0" * 64)

    def test_chunk_rejects_range_duration_alignment_and_size_errors(self) -> None:
        invalid_changes = (
            {"last_sample_exclusive": 1599},
            {"duration_ms": 101},
            {"payload": b"x" * (MAX_AUDIO_PAYLOAD_BYTES + 2)},
            {"captured_at_wall_clock": "2026-07-15T20:00:00"},
            {"sequence": True},
            {"duration_ms": 100.0},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(ProtocolViolation):
                    self.make_chunk(**changes)


class TerminalLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = StreamKey("session_test", "stream_mic", 0, Source.MICROPHONE)

    def transcript(
        self,
        first_sequence: int,
        last_sequence: int,
        first_sample: int,
        last_sample: int,
        attempt: int = 0,
    ) -> TerminalOutcome:
        return TerminalOutcome(
            TerminalKind.TRANSCRIPT,
            KnownCoverage(
                self.key,
                first_sequence,
                last_sequence,
                first_sample,
                last_sample,
            ),
            stt_attempt_generation=attempt,
        )

    def test_adjacent_ranges_commit_and_exact_retry_is_idempotent(self) -> None:
        ledger = TerminalLedger()
        first = self.transcript(0, 0, 0, 800)
        adjacent = self.transcript(1, 1, 800, 1600)
        self.assertTrue(ledger.commit(first))
        self.assertFalse(ledger.commit(self.transcript(0, 0, 0, 800, attempt=8)))
        self.assertTrue(ledger.commit(adjacent))
        self.assertEqual(len(ledger.outcomes(self.key)), 2)

    def test_sample_or_sequence_overlap_is_rejected(self) -> None:
        ledger = TerminalLedger()
        ledger.commit(self.transcript(0, 1, 0, 1600))
        for conflicting in (
            self.transcript(1, 2, 1600, 2400),
            self.transcript(2, 2, 1500, 2400),
            TerminalOutcome(
                TerminalKind.GAP,
                KnownCoverage(self.key, 0, 1, 0, 1600),
                2,
                reason_code="unknown_forwarding_state",
            ),
        ):
            with self.subTest(conflicting=conflicting):
                with self.assertRaises(ProtocolViolation):
                    ledger.commit(conflicting)

    def test_sequence_and_sample_order_must_agree(self) -> None:
        ledger = TerminalLedger()
        ledger.commit(self.transcript(0, 0, 800, 1600))
        with self.assertRaises(ProtocolViolation):
            ledger.commit(self.transcript(1, 1, 0, 800))

    def test_unknown_end_gap_blocks_tail_but_allows_strict_prefix(self) -> None:
        ledger = TerminalLedger()
        prefix = self.transcript(0, 1, 0, 1600)
        unknown = TerminalOutcome(
            TerminalKind.GAP,
            UnknownEndCoverage(self.key, 2, 1600),
            1,
            reason_code="process_terminated",
        )
        self.assertTrue(ledger.commit(prefix))
        self.assertTrue(ledger.commit(unknown))
        with self.assertRaises(ProtocolViolation):
            ledger.commit(self.transcript(2, 2, 1600, 2400))

    def test_transcript_cannot_claim_unknown_coverage(self) -> None:
        with self.assertRaises(ProtocolViolation):
            TerminalOutcome(
                TerminalKind.TRANSCRIPT,
                UnknownEndCoverage(self.key, 2, 1600),
                1,
            )


if __name__ == "__main__":
    unittest.main()

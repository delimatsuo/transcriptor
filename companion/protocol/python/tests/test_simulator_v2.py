from dataclasses import replace
import unittest

from tars_phase2.model import ProtocolV2Violation
from tars_phase2.simulator import (
    AdmissionAuthority,
    AdmissionRejected,
    CoverageState,
    DeletionFence,
    DeletionState,
    DerivedDisplayState,
    EffectState,
    HierarchicalIngressQuota,
    LifecycleProjection,
    PhysicalCaptureState,
    ProviderEffectFence,
    QuotaLimits,
    RawCustodyBuffer,
    ScopeQuotaLimits,
    TokenBucketQuota,
    TransportEdgeBudget,
    TransportState,
)


def quota_rows(process_event_burst=3_200):
    return {
        "source": ScopeQuotaLimits(50, 100, 192_000, 384_000, 205_000, 410_000, 1_048_576, 1_048_576, 1, 1, 1),
        "session": ScopeQuotaLimits(100, 200, 384_000, 768_000, 410_000, 820_000, 2_097_152, 2_097_152, 1, 2, 2),
        "tenant": ScopeQuotaLimits(400, 800, 1_536_000, 3_072_000, 1_640_000, 3_280_000, 8_388_608, 8_388_608, 4, 8, 8),
        "process": ScopeQuotaLimits(1_600, process_event_burst, 6_144_000, 12_288_000, 6_560_000, 13_120_000, 33_554_432, 33_554_432, 16, 32, 32),
    }


class ProtocolV2SimulatorTests(unittest.TestCase):
    def test_deletion_crash_resume_two_pass_absence_and_late_callback_fencing(self):
        deletion = DeletionFence(
            workers={"worker"}, callbacks={"callback-lane"}, connections={"connection"},
            effects={"effect"}, stores={"session", "retry", "transcript", "backup"},
        )
        generation = deletion.request()
        with self.assertRaises(ProtocolV2Violation):
            deletion.assert_admission_allowed()
        with self.assertRaises(ProtocolV2Violation):
            deletion.reject_late_callback(generation - 1)
        self.assertEqual(deletion.late_callback_rejections, 1)
        deletion.acknowledge_worker("worker", generation)
        deletion.acknowledge_callback("callback-lane", generation)
        deletion.acknowledge_connection("connection", generation)
        with self.assertRaises(ProtocolV2Violation):
            deletion.acknowledge_worker("unregistered-worker", generation)
        with self.assertRaises(ProtocolV2Violation):
            deletion.start_deleting()
        deletion.acknowledge_effect("effect", generation)
        with self.assertRaises(ProtocolV2Violation):
            deletion.start_deleting(expected_workers={"worker", "unregistered-worker"})
        deletion.start_deleting()

        restarted = DeletionFence.restore(deletion.snapshot())
        absent = {name: True for name in restarted.stores}
        self.assertTrue(restarted.record_absence_pass(1, absent))
        failed = dict(absent)
        failed["backup"] = False
        self.assertFalse(restarted.record_absence_pass(2, failed))
        self.assertEqual(restarted.state, DeletionState.DELETION_FAILED)
        with self.assertRaises(ProtocolV2Violation):
            restarted.finish()

        resumed = DeletionFence.restore(restarted.snapshot())
        resumed.resume(generation)
        self.assertTrue(resumed.record_absence_pass(2, absent))
        self.assertTrue(resumed.record_absence_pass(2, absent))
        resumed.finish()
        self.assertEqual(resumed.state, DeletionState.DELETED)
        with self.assertRaises(ProtocolV2Violation):
            resumed.reject_late_callback(generation)
        self.assertEqual(resumed.late_callback_rejections, 2)

    def test_single_use_effect_durable_owner_journal_and_fence_recovery(self):
        effect = ProviderEffectFence("effect-1")
        token = effect.prepare("owner-a")
        self.assertEqual(effect.prepare("owner-a"), token)
        with self.assertRaises(ProtocolV2Violation):
            effect.prepare("owner-b")
        effect.invoke(token)
        self.assertEqual(effect.invoke_count, 1)
        with self.assertRaises(ProtocolV2Violation):
            effect.invoke(token)
        effect.provider_ack(token)
        self.assertFalse(effect.forwarded)
        effect.commit_journal(token)
        self.assertTrue(effect.forwarded)
        with self.assertRaises(ProtocolV2Violation):
            effect.acknowledge_provider_close()
        with self.assertRaises(ProtocolV2Violation):
            effect.acknowledge_owner_termination()

        restarted = ProviderEffectFence.restore(effect.snapshot())
        restarted.recovery_epoch(1, 1)
        self.assertEqual(restarted.state, EffectState.EFFECT_QUIESCENCE_REQUIRED)
        self.assertTrue(restarted.forwarded)
        with self.assertRaises(ProtocolV2Violation):
            restarted.callback(token)
        with self.assertRaises(ProtocolV2Violation):
            restarted.prepare("recovery-owner")
        restarted.acknowledge_provider_close()
        with self.assertRaises(ProtocolV2Violation):
            restarted.terminalize()
        restarted.acknowledge_owner_termination()
        restarted.terminalize()
        self.assertEqual(restarted.state, EffectState.TERMINAL)
        self.assertEqual(restarted.invoke_count, 1)
        with self.assertRaises(ProtocolV2Violation):
            restarted.recovery_epoch(2, 2)

    def test_quota_rejects_before_custody_allocation_and_burns_attempt_tokens(self):
        quota = TokenBucketQuota(QuotaLimits(2, 2, 100, 100, 100, 100, 500))
        self.assertTrue(quota.reserve(0, events=1, payload_bytes=50, metadata_bytes=20, custody_bytes=200))
        self.assertFalse(quota.reserve(0, events=2, payload_bytes=50, metadata_bytes=20, custody_bytes=200))
        self.assertEqual(quota.custody, 200)
        self.assertFalse(quota.reserve(0, events=1, payload_bytes=1, metadata_bytes=1, custody_bytes=1))
        self.assertTrue(quota.reserve(1, events=1, payload_bytes=1, metadata_bytes=1, custody_bytes=1))
        with self.assertRaises(ProtocolV2Violation):
            quota.reserve(1.5, events=1, payload_bytes=1, metadata_bytes=1, custody_bytes=1)

    def test_all_quota_rows_are_atomic_and_broader_failure_mutates_no_custody(self):
        quota = HierarchicalIngressQuota(quota_rows(process_event_burst=0))
        self.assertFalse(quota.reserve(
            0, events=1, payload_bytes=320, metadata_bytes=476,
            custody_bytes=320, resident_bytes=1_024,
        ))
        self.assertTrue(all(scope.custody == 0 and scope.resident == 0 for scope in quota.scopes.values()))
        self.assertTrue(all(scope.events == max(0, scope.limits.event_burst - 1) for scope in quota.scopes.values()))
        with self.assertRaises(ProtocolV2Violation):
            ScopeQuotaLimits(-1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)

        quota = HierarchicalIngressQuota(quota_rows())
        self.assertTrue(quota.reserve(
            0, events=1, payload_bytes=320, metadata_bytes=476,
            custody_bytes=320, resident_bytes=1_024, active_sessions=1, writable_attempts=1,
        ))
        self.assertFalse(quota.reserve(
            0, events=1, payload_bytes=320, metadata_bytes=476,
            custody_bytes=320, resident_bytes=1_024, active_sessions=1,
        ))
        quota.release(custody_bytes=320, resident_bytes=1_024, active_sessions=1, writable_attempts=1)
        self.assertTrue(all(scope.custody == 0 and scope.sessions == 0 for scope in quota.scopes.values()))
        with self.assertRaises(ProtocolV2Violation):
            quota.reserve(
                0.5, events=1, payload_bytes=320, metadata_bytes=476,
                custody_bytes=320, resident_bytes=1_024,
            )
        with self.assertRaises(ProtocolV2Violation):
            quota.release(custody_bytes=True)

    def test_admission_matrix_fails_with_one_non_enumerating_code(self):
        authority = AdmissionAuthority(
            "capture", "tenant", "actor", "enrollment", "session", "stream", 4, 8, 2,
            "notice-v3", "consent", 10_000,
        )
        request = {
            "audience": "capture", "tenantId": "tenant", "actorId": "actor",
            "enrollmentId": "enrollment", "sessionId": "session", "streamId": "stream",
            "captureGeneration": 4, "fence": 8, "protocolVersion": 2,
            "noticeVersion": "notice-v3", "legalBasis": "consent",
        }
        authority.authorize(request, 9_999)
        for name in request:
            changed = dict(request)
            changed[name] = "wrong" if not isinstance(changed[name], int) else changed[name] + 1
            with self.assertRaises(AdmissionRejected) as failure:
                authority.authorize(changed, 9_999)
            self.assertEqual(failure.exception.public_code, "capture_not_authorized")
        for invalid in (replace(authority, authenticated=False), replace(authority, revoked=True)):
            with self.assertRaises(AdmissionRejected):
                invalid.authorize(request, 9_999)
        with self.assertRaises(AdmissionRejected):
            authority.authorize(request, 10_000)
        for name, invalid_value in (("captureGeneration", 4.0), ("fence", True), ("protocolVersion", 2.0)):
            changed = dict(request)
            changed[name] = invalid_value
            with self.assertRaises(AdmissionRejected):
                authority.authorize(changed, 9_999)
        for invalid_now in (True, 9_999.5):
            with self.assertRaises(AdmissionRejected):
                authority.authorize(request, invalid_now)

    def test_custody_limits_forwarding_discard_and_wall_clock_release(self):
        custody = RawCustodyBuffer(8_000, 1)
        payload = bytes(range(160)) * 2
        self.assertTrue(custody.reserve(
            "event-0", payload, frames=160, metadata_bytes=472,
            resident_overhead_bytes=128, captured_at_ms=0,
        ))
        self.assertFalse(custody.reserve(
            "event-0", payload, frames=160, metadata_bytes=472,
            resident_overhead_bytes=128, captured_at_ms=0,
        ))
        with self.assertRaises(ProtocolV2Violation):
            custody.acknowledge_forwarded("event-0", journal_committed=False)
        custody.acknowledge_forwarded("event-0", journal_committed=True)
        self.assertIn("event-0", custody.forwarded)
        self.assertEqual(custody.retained_payload_bytes, 0)

        custody = RawCustodyBuffer(8_000, 1)
        custody.reserve("event-1", payload, frames=160, metadata_bytes=472, resident_overhead_bytes=128, captured_at_ms=0)
        custody.advance_clock(10_000)
        self.assertTrue(custody.acquisition_stopped)
        self.assertEqual(custody.read("event-1", 29_999), payload)
        custody.advance_clock(30_000)
        self.assertEqual(custody.retained_payload_bytes, 0)
        self.assertEqual(custody.released["event-1"], "privacy_timeout_local")
        self.assertNotIn("event-1", custody.forwarded)

        custody = RawCustodyBuffer(48_000, 2)
        with self.assertRaises(ProtocolV2Violation):
            custody.reserve(
                "oversized", bytes(384_000), frames=96_000, metadata_bytes=472,
                resident_overhead_bytes=128, captured_at_ms=0,
            )
        self.assertEqual(custody.retained_frames, 0)
        stereo = bytes(19_200)
        for index in range(10):
            custody.reserve(
                f"event-{index}", stereo, frames=4_800, metadata_bytes=4_096,
                resident_overhead_bytes=256, captured_at_ms=index * 100,
            )
        self.assertEqual(custody.retained_frames, 48_000)
        custody.advance_clock(1_000, clock_certain=False)
        self.assertEqual(custody.retained_frames, 0)
        self.assertEqual(len(custody.gap_obligations), 10)

        custody = RawCustodyBuffer(8_000, 1)
        custody.reserve("discard", payload, frames=160, metadata_bytes=472, resident_overhead_bytes=128, captured_at_ms=0)
        custody.acknowledge_durable_discard("discard", "gap-1")
        custody.acknowledge_durable_discard("discard", "gap-1")
        with self.assertRaises(ProtocolV2Violation):
            custody.acknowledge_durable_discard("discard", "gap-2")
        self.assertEqual(custody.gap_obligations["discard"], "gap-1")

        for invalid in (True, 160.5):
            custody = RawCustodyBuffer(8_000, 1)
            with self.assertRaises(ProtocolV2Violation):
                custody.reserve(
                    "invalid", payload, frames=invalid, metadata_bytes=472,
                    resident_overhead_bytes=128, captured_at_ms=0,
                )
            self.assertEqual(custody.retained_frames, 0)
        with self.assertRaises(ProtocolV2Violation):
            RawCustodyBuffer(8_000, 1).advance_clock(0.5)

    def test_local_privacy_release_fences_pending_provider_effect(self):
        payload = bytes(range(160)) * 2
        custody = RawCustodyBuffer(8_000, 1)
        custody.reserve(
            "prepared-discard", payload, frames=160, metadata_bytes=472,
            resident_overhead_bytes=128, captured_at_ms=0,
        )
        prepared = ProviderEffectFence("effect-prepared-discard")
        prepared_token = prepared.prepare("owner-a")
        custody.register_effect("prepared-discard", prepared)
        with self.assertRaises(ProtocolV2Violation):
            custody.acknowledge_durable_discard("prepared-discard", "gap-deletion")
        custody.cancel_prepared_effect_and_discard("prepared-discard", prepared, "gap-deletion")
        custody.cancel_prepared_effect_and_discard("prepared-discard", prepared, "gap-deletion")
        self.assertEqual(custody.gap_obligations["prepared-discard"], "gap-deletion")
        self.assertTrue(prepared.cancelled_without_invoke)
        with self.assertRaises(ProtocolV2Violation):
            prepared.invoke(prepared_token)
        with self.assertRaises(ProtocolV2Violation):
            prepared.callback(prepared_token)
        with self.assertRaises(ProtocolV2Violation):
            custody.invoke_effect("prepared-discard", prepared, prepared_token)

        custody = RawCustodyBuffer(8_000, 1)
        custody.reserve(
            "forwarded", payload, frames=160, metadata_bytes=472,
            resident_overhead_bytes=128, captured_at_ms=0,
        )
        effect = ProviderEffectFence("effect-forwarded")
        token = effect.prepare("owner-a")
        custody.register_effect("forwarded", effect)
        custody.invoke_effect("forwarded", effect, token)
        with self.assertRaises(ProtocolV2Violation):
            custody.cancel_prepared_effect_and_discard("forwarded", effect, "gap-forbidden")
        with self.assertRaises(ProtocolV2Violation):
            custody.acknowledge_forwarded("forwarded", journal_committed=True)
        custody.local_privacy_release("forwarded", "emergency_local")
        self.assertIn("forwarded", custody.effect_pending_releases)
        self.assertNotIn("forwarded", custody.gap_obligations)
        with self.assertRaises(ProtocolV2Violation):
            custody.register_effect("forwarded", ProviderEffectFence("replacement"))
        with self.assertRaises(ProtocolV2Violation):
            custody.acknowledge_durable_discard("forwarded", "gap-forbidden")
        with self.assertRaises(ProtocolV2Violation):
            custody.resolve_pending_effect("forwarded", effect, "durable_discard")
        foreign = ProviderEffectFence("effect-forwarded")
        foreign_token = foreign.prepare("owner-b")
        foreign.invoke(foreign_token)
        foreign.provider_ack(foreign_token)
        foreign.commit_journal(foreign_token)
        with self.assertRaises(ProtocolV2Violation):
            custody.resolve_pending_effect("forwarded", foreign, "forwarded")
        effect.provider_ack(token)
        effect.commit_journal(token)
        custody.resolve_pending_effect("forwarded", effect, "forwarded")
        self.assertIn("forwarded", custody.forwarded)
        self.assertNotIn("forwarded", custody.gap_obligations)

        custody = RawCustodyBuffer(8_000, 1)
        custody.reserve(
            "ambiguous", payload, frames=160, metadata_bytes=472,
            resident_overhead_bytes=128, captured_at_ms=0,
        )
        effect = ProviderEffectFence("effect-ambiguous")
        token = effect.prepare("owner-a")
        custody.register_effect("ambiguous", effect)
        custody.invoke_effect("ambiguous", effect, token)
        custody.local_privacy_release("ambiguous", "deletion_local")
        effect.recovery_epoch(1, 1)
        effect.acknowledge_provider_close()
        effect.acknowledge_owner_termination()
        effect.terminalize()
        custody.resolve_pending_effect("ambiguous", effect, "ambiguous_effect")
        self.assertEqual(custody.gap_obligations["ambiguous"], "ambiguous_effect")
        self.assertNotIn("ambiguous", custody.forwarded)

    def test_transport_edge_bounds_pre_auth_audio_and_deadlines(self):
        edge = TransportEdgeBudget()
        with self.assertRaises(ProtocolV2Violation):
            edge.open_pending(
                "fractional", "192.0.2.1", 0.5,
                header_bytes=1, first_auth_bytes=1, receive_buffer_bytes=1,
            )
        with self.assertRaises(ProtocolV2Violation):
            edge.open_pending(
                "boolean", "192.0.2.1", 0,
                header_bytes=True, first_auth_bytes=1, receive_buffer_bytes=1,
            )
        for index in range(16):
            edge.open_pending(
                f"connection-{index}", "192.0.2.1", 0,
                header_bytes=16_384, first_auth_bytes=8_192, receive_buffer_bytes=32_768,
            )
        before = edge.pending_bytes
        with self.assertRaises(ProtocolV2Violation):
            edge.open_pending(
                "connection-17", "192.0.2.1", 0,
                header_bytes=1, first_auth_bytes=1, receive_buffer_bytes=1,
            )
        with self.assertRaises(ProtocolV2Violation):
            edge.reject_pre_auth_audio(68_100)
        self.assertEqual(edge.pending_bytes, before)
        edge.authenticate("connection-0", 8_000)
        self.assertEqual(edge.authenticated_parser_bytes, 68_100)
        with self.assertRaises(ProtocolV2Violation):
            edge.authenticate("connection-1", 8_001)
        edge.open_pending(
            "future-clock", "192.0.2.2", 10_000,
            header_bytes=1, first_auth_bytes=1, receive_buffer_bytes=1,
        )
        with self.assertRaises(ProtocolV2Violation):
            edge.authenticate("future-clock", 9_999)
        with self.assertRaises(ProtocolV2Violation):
            edge.authenticate("future-clock", 10_000.5)
        with self.assertRaises(ProtocolV2Violation):
            edge.reject_pre_auth_audio(True)

    def test_lifecycle_axes_are_origin_separated_and_conservative(self):
        lifecycle = LifecycleProjection()
        self.assertEqual(lifecycle.derived(), DerivedDisplayState.DEGRADED)
        lifecycle.companion(1, PhysicalCaptureState.RECORDING)
        lifecycle.gateway_transport(1, TransportState.FORWARDING)
        lifecycle.gateway_coverage(1, CoverageState.OPEN)
        self.assertEqual(lifecycle.derived(), DerivedDisplayState.RECORDING)
        lifecycle.gateway_coverage(2, CoverageState.COMPLETED)
        self.assertEqual(lifecycle.derived(), DerivedDisplayState.FINALIZING)
        with self.assertRaises(ProtocolV2Violation):
            lifecycle.assert_upgrade_allowed()
        lifecycle.companion(2, PhysicalCaptureState.STOPPED)
        lifecycle.gateway_transport(2, TransportState.CLOSED)
        self.assertEqual(lifecycle.derived(), DerivedDisplayState.COMPLETED)
        lifecycle.assert_upgrade_allowed()
        lifecycle.gateway_coverage(3, CoverageState.DELETE_QUIESCING)
        self.assertEqual(lifecycle.derived(), DerivedDisplayState.DELETING)
        with self.assertRaises(ProtocolV2Violation):
            lifecycle.companion(2, PhysicalCaptureState.DEGRADED)
        with self.assertRaises(ProtocolV2Violation):
            lifecycle.companion(True, PhysicalCaptureState.DEGRADED)
        with self.assertRaises(ProtocolV2Violation):
            lifecycle.gateway_transport(2.5, TransportState.CLOSED)


if __name__ == "__main__":
    unittest.main()

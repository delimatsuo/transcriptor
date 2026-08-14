import unittest

from tars_phase2.simulator import DeletionFence, DeletionState, EffectState, ProviderEffectFence, QuotaLimits, TokenBucketQuota
from tars_phase2.model import ProtocolV2Violation


class ProtocolV2SimulatorTests(unittest.TestCase):
    def test_deletion_fences_admission_until_positive_quiescence(self):
        deletion = DeletionFence()
        generation = deletion.request()
        with self.assertRaises(ProtocolV2Violation):
            deletion.assert_admission_allowed()
        with self.assertRaises(ProtocolV2Violation):
            deletion.start_deleting({"worker"}, {"callback"})
        deletion.acknowledge_worker("worker", generation)
        with self.assertRaises(ProtocolV2Violation):
            deletion.start_deleting({"worker"}, {"callback"})
        deletion.acknowledge_callback("callback", generation)
        deletion.start_deleting({"worker"}, {"callback"})
        self.assertEqual(deletion.state, DeletionState.DELETING)
        deletion.finish()
        self.assertEqual(deletion.state, DeletionState.DELETED)

    def test_single_use_effect_and_fence_recovery(self):
        effect = ProviderEffectFence("effect-1")
        token = effect.prepare("owner-a")
        effect.invoke(token)
        self.assertEqual(effect.invoke_count, 1)
        with self.assertRaises(ProtocolV2Violation):
            effect.invoke(token)
        effect.recovery_epoch(1, 1)
        self.assertEqual(effect.state, EffectState.EFFECT_QUIESCENCE_REQUIRED)
        with self.assertRaises(ProtocolV2Violation):
            effect.callback(token)
        effect.acknowledge_provider_close()
        with self.assertRaises(ProtocolV2Violation):
            effect.terminalize()
        effect.acknowledge_owner_termination()
        effect.terminalize()
        self.assertEqual(effect.state, EffectState.TERMINAL)

    def test_quota_rejects_before_custody_allocation_and_burns_attempt_tokens(self):
        quota = TokenBucketQuota(QuotaLimits(2, 2, 100, 100, 100, 100, 500))
        self.assertTrue(quota.reserve(0, events=1, payload_bytes=50, metadata_bytes=20, custody_bytes=200))
        self.assertFalse(quota.reserve(0, events=2, payload_bytes=50, metadata_bytes=20, custody_bytes=200))
        self.assertEqual(quota.custody, 200)
        self.assertFalse(quota.reserve(0, events=1, payload_bytes=1, metadata_bytes=1, custody_bytes=1))
        self.assertTrue(quota.reserve(1, events=1, payload_bytes=1, metadata_bytes=1, custody_bytes=1))


if __name__ == "__main__":
    unittest.main()

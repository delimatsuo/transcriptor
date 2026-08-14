import unittest

from tars_phase2.model import MAX_CUSTODY_FRAMES, MAX_AUDIO_PAYLOAD_BYTES, MAX_QUEUED_AUDIO_EVENTS
from tars_phase2.simulator import QuotaLimits, TokenBucketQuota


class ProtocolV2LongDurationTests(unittest.TestCase):
    def test_simultaneous_mixed_rate_sources_stay_bounded_for_60_90_120_minutes(self):
        sources = ((8000, 1), (48000, 2))
        for minutes in (60, 90, 120):
            source_quotas = [
                TokenBucketQuota(QuotaLimits(50, 100, 192000, 384000, 205000, 410000, 1_048_576)),
                TokenBucketQuota(QuotaLimits(50, 100, 192000, 384000, 205000, 410000, 1_048_576)),
            ]
            session_quota = TokenBucketQuota(QuotaLimits(100, 200, 384000, 768000, 410000, 820000, 2_097_152))
            tenant_quota = TokenBucketQuota(QuotaLimits(400, 800, 1_536_000, 3_072_000, 1_640_000, 3_280_000, 8_388_608))
            process_quota = TokenBucketQuota(QuotaLimits(1_600, 3_200, 6_144_000, 12_288_000, 6_560_000, 13_120_000, 33_554_432))
            descriptors = []
            for rate, channels in sources:
                chunk_ms = 20
                frames = rate * chunk_ms // 1000
                payload = frames * channels * 2
                events = minutes * 60 * 1000 // chunk_ms
                self.assertLessEqual(frames, min(MAX_CUSTODY_FRAMES, 2 * rate))
                self.assertLessEqual(payload, MAX_AUDIO_PAYLOAD_BYTES)
                self.assertEqual(events, minutes * 60 * 50)
                self.assertGreater(events, MAX_QUEUED_AUDIO_EVENTS)
                descriptors.append((frames, payload, events))
            for event in range(descriptors[0][2]):
                second = event // 50
                for source_index, (_, payload, _) in enumerate(descriptors):
                    self.assertTrue(source_quotas[source_index].reserve(
                            second,
                            events=1,
                            payload_bytes=payload,
                            metadata_bytes=4_100,
                            custody_bytes=payload,
                        ))
                    self.assertTrue(session_quota.reserve(
                        second,
                        events=1,
                        payload_bytes=payload,
                        metadata_bytes=4_100,
                        custody_bytes=payload,
                    ))
                    self.assertTrue(tenant_quota.reserve(
                        second, events=1, payload_bytes=payload,
                        metadata_bytes=4_100, custody_bytes=payload,
                    ))
                    self.assertTrue(process_quota.reserve(
                        second, events=1, payload_bytes=payload,
                        metadata_bytes=4_100, custody_bytes=payload,
                    ))
                    source_quotas[source_index].release_custody(payload)
                    session_quota.release_custody(payload)
                    tenant_quota.release_custody(payload)
                    process_quota.release_custody(payload)
            self.assertEqual(source_quotas[0].custody, 0)
            self.assertEqual(source_quotas[1].custody, 0)
            self.assertEqual(session_quota.custody, 0)
            self.assertEqual(tenant_quota.custody, 0)
            self.assertEqual(process_quota.custody, 0)

    def test_quota_and_custody_reservation_do_not_grow_with_duration(self):
        quota = TokenBucketQuota(QuotaLimits(100, 200, 384000, 768000, 410000, 820000, 1_048_576))
        for second in range(120):
            for _ in range(8):
                self.assertTrue(quota.reserve(second, events=1, payload_bytes=12_000, metadata_bytes=1_000, custody_bytes=12_000))
                quota.release_custody(12_000)
        self.assertEqual(quota.custody, 0)
        self.assertLessEqual(quota.custody, quota.limits.custody_bytes)


if __name__ == "__main__":
    unittest.main()

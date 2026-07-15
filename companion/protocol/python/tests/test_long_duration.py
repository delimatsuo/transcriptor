"""Long-duration bounded-memory conformance for fixed synthetic bytes."""

import unittest

from tars_phase1a.fixtures import load_committed_catalog
from tars_phase1a.model import AudioChunk, Source, StreamKey
from tars_phase1a.simulator import FakePrincipal, OfflineProtocolSimulator


CHUNK_DURATION_MS = 100
CHUNK_SAMPLES = 1600
CHUNK_BYTES = 3200
BATCH_CHUNKS = 20
CLIENT_BUFFER_BYTES = CHUNK_BYTES * BATCH_CHUNKS
GATEWAY_BUFFER_BYTES = CHUNK_BYTES * BATCH_CHUNKS


class LongDurationConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = FakePrincipal("org_fixture", "user_fixture")
        cls.payload = load_committed_catalog().generate("counter-3200-v1")

    def run_duration(self, minutes: int, source: Source):  # type: ignore[no-untyped-def]
        total_chunks = minutes * 60 * 1000 // CHUNK_DURATION_MS
        self.assertEqual(total_chunks % BATCH_CHUNKS, 0)
        total_batches = total_chunks // BATCH_CHUNKS
        key = StreamKey(
            "session_long_{}".format(minutes),
            "stream_{}".format(source.value),
            0,
            source,
        )
        simulator = OfflineProtocolSimulator(
            key.session_id,
            self.owner,
            max_client_buffer_bytes=CLIENT_BUFFER_BYTES,
            max_gateway_queue_bytes=GATEWAY_BUFFER_BYTES,
        )

        max_client_bytes = 0
        max_gateway_bytes = 0
        for batch in range(total_batches):
            first_sequence = batch * BATCH_CHUNKS
            for sequence in range(first_sequence, first_sequence + BATCH_CHUNKS):
                first_sample = sequence * CHUNK_SAMPLES
                chunk = AudioChunk(
                    key=key,
                    sequence=sequence,
                    first_sample=first_sample,
                    last_sample_exclusive=first_sample + CHUNK_SAMPLES,
                    captured_at_monotonic_ns=(
                        sequence * CHUNK_DURATION_MS * 1_000_000
                    ),
                    captured_at_wall_clock="2026-07-15T20:00:00Z",
                    sample_rate_hertz=16_000,
                    channel_count=1,
                    duration_ms=CHUNK_DURATION_MS,
                    payload=self.payload,
                )
                self.assertTrue(simulator.submit(self.owner, chunk))

            stream_diagnostics = simulator.diagnostics()["streams"][0]
            client_bytes = stream_diagnostics["clientPayloadBytes"]
            gateway_bytes = stream_diagnostics["gatewayPayloadBytes"]
            max_client_bytes = max(max_client_bytes, client_bytes)
            max_gateway_bytes = max(max_gateway_bytes, gateway_bytes)
            self.assertLessEqual(client_bytes, CLIENT_BUFFER_BYTES)
            self.assertLessEqual(gateway_bytes, GATEWAY_BUFFER_BYTES)

            coverage = simulator.provider_write(
                self.owner,
                key,
                count=BATCH_CHUNKS,
            )
            simulator.journal_provider_write(self.owner, key)
            _, committed = simulator.commit_transcript(self.owner, key, coverage)
            self.assertTrue(committed)
            self.assertEqual(simulator.raw_payload_bytes(key), 0)

            if batch and batch % 150 == 0:
                simulator.rotate_stt_attempt(self.owner, key)
            if batch and batch % 300 == 0:
                self.assertEqual(
                    simulator.reconnect(self.owner, key).resend_ranges,
                    (),
                )

        watermarks = simulator.watermarks(key)
        expected_last_sequence = total_chunks - 1
        expected_last_sample = total_chunks * CHUNK_SAMPLES
        expected_last_time = (
            expected_last_sequence * CHUNK_DURATION_MS * 1_000_000
        )
        for watermark in (
            watermarks.admitted,
            watermarks.forwarded,
            watermarks.durable_transcript,
        ):
            self.assertEqual(watermark.sequence, expected_last_sequence)
            self.assertEqual(watermark.last_sample_exclusive, expected_last_sample)
            self.assertEqual(
                watermark.captured_at_monotonic_ns,
                expected_last_time,
            )

        diagnostics = simulator.diagnostics()["streams"][0]
        self.assertEqual(diagnostics["clientQueueDepth"], 0)
        self.assertEqual(diagnostics["gatewayQueueDepth"], 0)
        self.assertEqual(diagnostics["clientPayloadBytes"], 0)
        self.assertEqual(diagnostics["gatewayPayloadBytes"], 0)
        self.assertEqual(diagnostics["terminalOutcomeCount"], total_batches)
        self.assertEqual(diagnostics["forwardingRecordCount"], total_batches)
        self.assertEqual(max_client_bytes, CLIENT_BUFFER_BYTES)
        self.assertEqual(max_gateway_bytes, GATEWAY_BUFFER_BYTES)
        return {
            "minutes": minutes,
            "source": source.value,
            "chunks": total_chunks,
            "batches": total_batches,
            "lastSequence": expected_last_sequence,
            "lastSampleExclusive": expected_last_sample,
            "lastCapturedAtMonotonicNs": expected_last_time,
            "maxClientPayloadBytes": max_client_bytes,
            "maxGatewayPayloadBytes": max_gateway_bytes,
            "finalRawPayloadBytes": simulator.raw_payload_bytes(key),
        }

    def test_60_and_90_minute_runs_remain_bounded_and_complete(self) -> None:
        results = (
            self.run_duration(60, Source.MICROPHONE),
            self.run_duration(90, Source.SYSTEM_AUDIO),
        )
        self.assertEqual(
            results,
            (
                {
                    "minutes": 60,
                    "source": "microphone",
                    "chunks": 36_000,
                    "batches": 1_800,
                    "lastSequence": 35_999,
                    "lastSampleExclusive": 57_600_000,
                    "lastCapturedAtMonotonicNs": 3_599_900_000_000,
                    "maxClientPayloadBytes": 64_000,
                    "maxGatewayPayloadBytes": 64_000,
                    "finalRawPayloadBytes": 0,
                },
                {
                    "minutes": 90,
                    "source": "system_audio",
                    "chunks": 54_000,
                    "batches": 2_700,
                    "lastSequence": 53_999,
                    "lastSampleExclusive": 86_400_000,
                    "lastCapturedAtMonotonicNs": 5_399_900_000_000,
                    "maxClientPayloadBytes": 64_000,
                    "maxGatewayPayloadBytes": 64_000,
                    "finalRawPayloadBytes": 0,
                },
            ),
        )


if __name__ == "__main__":
    unittest.main()

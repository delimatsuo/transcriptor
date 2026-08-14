import json
import hashlib
import unittest
from pathlib import Path

from tars_phase2.framing import audio_metadata, encode_audio_frame, retry_commitment
from tars_phase2.model import AudioChunkV2, AtomicCoverage, Source, StreamKey, canonical_json_bytes, terminal_coverage_id, transcript_segment_id


class ProtocolV2VectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / "vectors" / "protocol-v2-vectors.json"
        with path.open(encoding="utf-8") as handle:
            cls.vector = json.load(handle)

    def test_canonical_identity_vectors(self):
        stream = self.vector["stream"]
        key = StreamKey(stream["sessionId"], stream["streamId"], stream["captureGeneration"], Source(stream["source"]))
        atomic = tuple(
            AtomicCoverage(key, item["sequence"], item["firstSample"], item["lastSampleExclusive"])
            for item in self.vector["atomicCoverage"]
        )
        self.assertEqual([item.coverage_id for item in atomic], [item["coverageId"] for item in self.vector["atomicCoverage"]])
        self.assertEqual(terminal_coverage_id(key, atomic), self.vector["terminalCoverageId"])
        segment = self.vector["transcriptSegment"]
        self.assertEqual(
            transcript_segment_id(
                key,
                (atomic[0],),
                segment["textFirstSample"],
                segment["textLastSampleExclusive"],
                segment["providerResultOrdinal"],
                segment["providerName"],
                segment["providerResultId"],
                segment["sttAttemptGeneration"],
            ),
            segment["segmentId"],
        )

    def test_canonical_audio_frame_and_retry_vectors(self):
        stream = self.vector["stream"]
        key = StreamKey(stream["sessionId"], stream["streamId"], stream["captureGeneration"], Source(stream["source"]))
        vector = self.vector["audioFrame"]
        payload = bytes((index * 17 + 3) % 256 for index in range(vector["payloadBytes"]))
        chunk = AudioChunkV2(key, 0, 0, 160, 8_000, 1, 20, payload)
        metadata = canonical_json_bytes(audio_metadata(chunk))
        frame = encode_audio_frame(chunk)
        commitment = retry_commitment(bytes(range(32)), metadata, payload)
        self.assertEqual(metadata.decode("utf-8"), vector["canonicalMetadata"])
        self.assertEqual(len(metadata), vector["metadataBytes"])
        self.assertEqual(hashlib.sha256(metadata).hexdigest(), vector["metadataSha256"])
        self.assertEqual(len(frame), vector["frameBytes"])
        self.assertEqual(hashlib.sha256(frame).hexdigest(), vector["frameSha256"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), vector["payloadSha256"])
        self.assertEqual(commitment.hex(), vector["retryCommitmentHmacSha256"])


if __name__ == "__main__":
    unittest.main()

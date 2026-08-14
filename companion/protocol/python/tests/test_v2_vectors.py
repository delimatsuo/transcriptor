import json
import unittest
from pathlib import Path

from tars_phase2.model import AtomicCoverage, Source, StreamKey, terminal_coverage_id, transcript_segment_id


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


if __name__ == "__main__":
    unittest.main()

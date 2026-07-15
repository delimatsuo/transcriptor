"""Cross-language validation of the committed protocol-v1 vectors."""

import json
import unittest
from pathlib import Path

from tars_phase1a.fixtures import load_committed_catalog
from tars_phase1a.model import (
    AudioChunk,
    KnownCoverage,
    Source,
    StreamKey,
    TerminalKind,
    TerminalOutcome,
    UnknownEndCoverage,
    deterministic_event_id,
)


PROTOCOL_ROOT = Path(__file__).resolve().parents[2]
VECTOR_PATH = PROTOCOL_ROOT / "vectors" / "protocol-v1-vectors.json"
SCHEMA_PATH = PROTOCOL_ROOT / "schema" / "protocol-v1.schema.json"


class SharedVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with VECTOR_PATH.open("r", encoding="utf-8") as vector_file:
            cls.vector = json.load(vector_file)

    def key(self) -> StreamKey:
        stream = self.vector["stream"]
        return StreamKey(
            stream["sessionId"],
            stream["streamId"],
            stream["captureGeneration"],
            Source(stream["source"]),
        )

    def test_shared_identity_vectors_match_python(self) -> None:
        coverage_data = self.vector["knownCoverage"]
        coverage = KnownCoverage(
            self.key(),
            coverage_data["firstSequence"],
            coverage_data["lastSequenceInclusive"],
            coverage_data["firstSample"],
            coverage_data["lastSampleExclusive"],
            coverage_data["firstCapturedAtMonotonicNs"],
            coverage_data["lastCapturedAtMonotonicNs"],
        )
        self.assertEqual(coverage.coverage_id, coverage_data["coverageId"])
        self.assertEqual(
            deterministic_event_id("audio.chunk", self.key(), "0"),
            self.vector["expectedAudioEventId"],
        )
        self.assertEqual(
            TerminalOutcome(TerminalKind.TRANSCRIPT, coverage, 99).event_id,
            self.vector["expectedTranscriptTerminalId"],
        )

        unknown_data = self.vector["unknownGapMetadata"]["coverage"]
        unknown = UnknownEndCoverage(
            self.key(),
            unknown_data["firstSequence"],
            unknown_data["firstSample"],
            unknown_data["firstCapturedAtMonotonicNs"],
        )
        self.assertEqual(
            TerminalOutcome(
                TerminalKind.GAP,
                unknown,
                1,
                reason_code="process_terminated",
            ).event_id,
            self.vector["expectedUnknownGapTerminalId"],
        )

    def test_shared_audio_binding_matches_python_metadata(self) -> None:
        expected = self.vector["audioMetadata"]
        payload = load_committed_catalog().generate("counter-3200-v1")
        chunk = AudioChunk(
            key=self.key(),
            sequence=expected["sequence"],
            first_sample=expected["firstSample"],
            last_sample_exclusive=expected["lastSampleExclusive"],
            captured_at_monotonic_ns=expected["capturedAtMonotonicNs"],
            captured_at_wall_clock=expected["capturedAtWallClock"],
            sample_rate_hertz=expected["sampleRateHertz"],
            channel_count=expected["channelCount"],
            duration_ms=expected["durationMs"],
            payload=payload,
            device_id=expected["deviceId"],
        )
        self.assertEqual(chunk.metadata_dict(), expected)

    def test_shared_terminal_bindings_match_python_metadata(self) -> None:
        transcript_expected = self.vector["transcriptMetadata"]
        known = self.vector["knownCoverage"]
        coverage = KnownCoverage(
            self.key(),
            known["firstSequence"],
            known["lastSequenceInclusive"],
            known["firstSample"],
            known["lastSampleExclusive"],
            known["firstCapturedAtMonotonicNs"],
            known["lastCapturedAtMonotonicNs"],
        )
        transcript = TerminalOutcome(
            TerminalKind.TRANSCRIPT,
            coverage,
            transcript_expected["sttAttemptGeneration"],
            result_ordinal=transcript_expected["resultOrdinal"],
            device_id=transcript_expected["deviceId"],
            captured_at_monotonic_ns=transcript_expected[
                "capturedAtMonotonicNs"
            ],
            captured_at_wall_clock=transcript_expected["capturedAtWallClock"],
        )
        self.assertEqual(transcript.to_dict(), transcript_expected)

        gap_expected = self.vector["unknownGapMetadata"]
        unknown = gap_expected["coverage"]
        gap = TerminalOutcome(
            TerminalKind.GAP,
            UnknownEndCoverage(
                self.key(),
                unknown["firstSequence"],
                unknown["firstSample"],
                unknown["firstCapturedAtMonotonicNs"],
            ),
            gap_expected["sttAttemptGeneration"],
            result_ordinal=gap_expected["resultOrdinal"],
            reason_code=gap_expected["reasonCode"],
            device_id=gap_expected["deviceId"],
            captured_at_monotonic_ns=gap_expected["capturedAtMonotonicNs"],
            captured_at_wall_clock=gap_expected["capturedAtWallClock"],
        )
        self.assertEqual(gap.to_dict(), gap_expected)

    def test_schema_requires_exact_coverage_time_ranges(self) -> None:
        with SCHEMA_PATH.open("r", encoding="utf-8") as schema_file:
            definitions = json.load(schema_file)["$defs"]
        self.assertTrue(
            {
                "firstCapturedAtMonotonicNs",
                "lastCapturedAtMonotonicNs",
            }.issubset(definitions["knownCoverage"]["required"])
        )
        self.assertIn(
            "firstCapturedAtMonotonicNs",
            definitions["unknownEndCoverage"]["required"],
        )


if __name__ == "__main__":
    unittest.main()

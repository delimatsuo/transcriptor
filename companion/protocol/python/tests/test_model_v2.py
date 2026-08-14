import unittest

from tars_phase2.model import (
    AudioChunkV2,
    AtomicCoverage,
    CustodyLedger,
    Interval,
    IntervalSet,
    ProtocolV2Violation,
    Source,
    StreamKey,
    TerminalClaim,
    TerminalKind,
    TranscriptSegment,
    terminal_coverage_bytes,
    terminal_coverage_id,
    transcript_segment_id,
)


class ProtocolV2ModelTests(unittest.TestCase):
    def setUp(self):
        self.key = StreamKey("session-v2", "stream-mic", 4, Source.MICROPHONE)
        self.c0 = AtomicCoverage(self.key, 0, 0, 160)
        self.c1 = AtomicCoverage(self.key, 1, 160, 320)

    def test_audio_chunk_enforces_duration_and_rate_derived_custody(self):
        chunk = AudioChunkV2(self.key, 0, 0, 160, 8000, 1, 20, bytes(320))
        self.assertEqual(chunk.coverage, self.c0)
        with self.assertRaises(ProtocolV2Violation):
            AudioChunkV2(self.key, 0, 0, 161, 8000, 1, 20, bytes(322))

    def test_terminal_identity_is_ordered_full_atomic_list(self):
        first = terminal_coverage_id(self.key, (self.c0, self.c1))
        second = terminal_coverage_id(self.key, (self.c1, self.c0))
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("covr_"))
        self.assertGreater(len(terminal_coverage_bytes(self.key, (self.c0, self.c1))), 40)
        with self.assertRaises(ProtocolV2Violation):
            terminal_coverage_id(self.key, (self.c0, self.c0))

    def test_segment_identity_is_self_contained_and_text_independent(self):
        first = transcript_segment_id(self.key, (self.c0,), 20, 120, 0, "fixture", "result-1", 2)
        same_identity = transcript_segment_id(self.key, (self.c0,), 20, 120, 0, "fixture", "result-1", 2)
        different_bounds = transcript_segment_id(self.key, (self.c0,), 21, 120, 0, "fixture", "result-1", 2)
        self.assertEqual(first, same_identity)
        self.assertNotEqual(first, different_bounds)
        with self.assertRaises(ProtocolV2Violation):
            transcript_segment_id(self.key, (self.c0,), 0, 1, 0, "fixture", "result-1\u0301", None)

    def test_interval_set_rejects_overlap_but_preserves_sparse_ranges(self):
        intervals = IntervalSet((Interval(2, 2, 320, 480), Interval(0, 0, 0, 160)))
        self.assertEqual([item.first_sequence for item in intervals.intervals], [0, 2])
        with self.assertRaises(ProtocolV2Violation):
            IntervalSet((Interval(0, 1, 0, 320), Interval(1, 2, 160, 480)))

    def test_custody_release_cannot_cross_unresolved_gap(self):
        ledger = CustodyLedger(self.key)
        ledger.admit(self.c0)
        ledger.admit(self.c1)
        ledger.forward(self.c1)
        self.assertFalse(ledger.release_authorized(self.c1))
        ledger.forward(self.c0)
        self.assertTrue(ledger.release_authorized(self.c1))

    def test_two_finals_inside_one_atomic_chunk_share_coverage_but_have_segments(self):
        ledger = CustodyLedger(self.key)
        ledger.admit(self.c0)
        ledger.forward(self.c0)
        left = TranscriptSegment(self.key, (self.c0,), 0, 80, 0, "fixture", "r0", "hello", 1)
        right = TranscriptSegment(self.key, (self.c0,), 80, 160, 1, "fixture", "r1", "world", 1)
        self.assertTrue(ledger.commit_segment(left))
        self.assertTrue(ledger.commit_segment(right))
        claim = TerminalClaim(TerminalKind.TRANSCRIPT, self.key, (self.c0,), attempt_generation=1)
        self.assertTrue(ledger.commit_terminal(claim))
        self.assertFalse(ledger.commit_terminal(claim))

    def test_terminal_claim_rejects_unforwarded_and_conflicting_reuse(self):
        ledger = CustodyLedger(self.key)
        ledger.admit(self.c0)
        claim = TerminalClaim(TerminalKind.GAP, self.key, (self.c0,), reason="no_speech_final")
        self.assertTrue(ledger.commit_terminal(claim))
        with self.assertRaises(ProtocolV2Violation):
            ledger.forward(self.c0)
        conflicting = TerminalClaim(TerminalKind.GAP, self.key, (self.c0,), reason="durable_discard")
        with self.assertRaises(ProtocolV2Violation):
            ledger.commit_terminal(conflicting)


if __name__ == "__main__":
    unittest.main()

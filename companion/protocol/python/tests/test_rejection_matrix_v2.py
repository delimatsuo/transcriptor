import json
import re
import unittest
from pathlib import Path

from tars_phase2.model import (
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
    canonical_json_bytes,
    parse_canonical_json_bytes,
    terminal_coverage_id,
    transcript_segment_id,
)
from tars_phase2.simulator import DeletionFence, ProviderEffectFence


class ProtocolV2RejectionMatrixTests(unittest.TestCase):
    def setUp(self):
        self.key = StreamKey("session-v2", "stream-mic", 4, Source.MICROPHONE)
        self.c0 = AtomicCoverage(self.key, 0, 0, 160)
        self.c1 = AtomicCoverage(self.key, 1, 160, 320)

    def test_canonical_json_rejects_invalid_strings_numbers_and_keys(self):
        self.assertEqual(canonical_json_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}')
        self.assertEqual(
            canonical_json_bytes({"\ue000": 2, "\U0001f600": 1}),
            '{"\U0001f600":1,"\ue000":2}'.encode("utf-8"),
        )
        for value in (
            {"x": 1.0},
            {1: "x"},
            {"x": float("inf")},
            {"x": -1},
            {"x": 2**53},
            {"x": "e\u0301"},
            {"x": "bad\0value"},
            {"x": "\ud800"},
            {"x": "a" * 65_529},
        ):
            with self.assertRaises(ProtocolV2Violation):
                canonical_json_bytes(value)

        exact_control_envelope = {"x": "a" * 65_528}
        encoded = canonical_json_bytes(exact_control_envelope)
        self.assertEqual(len(encoded), 65_536)
        self.assertEqual(parse_canonical_json_bytes(encoded), exact_control_envelope)

    def test_canonical_json_parser_rejects_noncanonical_and_duplicate_bytes(self):
        canonical = b'{"a":1,"nested":[true,null,"ok"]}'
        self.assertEqual(parse_canonical_json_bytes(canonical), {"a": 1, "nested": [True, None, "ok"]})
        for payload in (
            b'{"a":1,"a":1}',
            b'{"b":2,"a":1}',
            b'{"a":1 }',
            b'{"a":-1}',
            b'{"a":9007199254740992}',
            b'{"a":1.0}',
            b'{"a":"bad\\u0000value"}',
            '{"\ue000":2,"\U0001f600":1}'.encode("utf-8"),
            b"[" * 1_100 + b"0" + b"]" * 1_100,
            b'\xff',
            b'',
        ):
            with self.assertRaises(ProtocolV2Violation):
                parse_canonical_json_bytes(payload)

    def test_uint64_boundaries_fail_closed(self):
        with self.assertRaises(ProtocolV2Violation):
            AtomicCoverage(self.key, 2**64, 0, 1)
        with self.assertRaises(ProtocolV2Violation):
            AtomicCoverage(self.key, True, 0, 1)
        with self.assertRaises(ProtocolV2Violation):
            AtomicCoverage(self.key, 0, 4, 4)

    def test_terminal_identity_rejects_duplicate_overlap_and_foreign_key(self):
        with self.assertRaises(ProtocolV2Violation):
            terminal_coverage_id(self.key, (self.c0, self.c0))
        overlapping = AtomicCoverage(self.key, 1, 80, 240)
        with self.assertRaises(ProtocolV2Violation):
            terminal_coverage_id(self.key, (self.c0, overlapping))
        foreign = AtomicCoverage(StreamKey("session-v2", "stream-other", 4, Source.MICROPHONE), 1, 160, 320)
        with self.assertRaises(ProtocolV2Violation):
            terminal_coverage_id(self.key, (self.c0, foreign))

        middle = AtomicCoverage(self.key, 1, 800, 960)
        nonadjacent_overlap = AtomicCoverage(self.key, 2, 80, 120)
        with self.assertRaises(ProtocolV2Violation):
            terminal_coverage_id(self.key, (self.c0, middle, nonadjacent_overlap))

    def test_segment_rejects_invalid_nfc_bounds_and_provenance(self):
        cases = (
            dict(text_first_sample=20, text_last_sample_exclusive=20, provider_name="fixture", provider_result_id="result"),
            dict(text_first_sample=0, text_last_sample_exclusive=1, provider_name="fixture", provider_result_id="result-e\u0301"),
            dict(text_first_sample=0, text_last_sample_exclusive=1, provider_name="fixture\0bad", provider_result_id="result"),
        )
        for case in cases:
            with self.assertRaises(ProtocolV2Violation):
                transcript_segment_id(self.key, (self.c0,), provider_result_ordinal=0, stt_attempt_generation=None, **case)

    def test_segment_replay_with_changed_text_is_rejected(self):
        ledger = CustodyLedger(self.key)
        ledger.admit(self.c0)
        ledger.forward(self.c0)
        first = TranscriptSegment(self.key, (self.c0,), 0, 80, 0, "fixture", "r0", "first", 1)
        changed = TranscriptSegment(self.key, (self.c0,), 0, 80, 0, "fixture", "r0", "changed", 1)
        self.assertEqual(first.segment_id, changed.segment_id)
        self.assertTrue(ledger.commit_segment(first))
        with self.assertRaises(ProtocolV2Violation):
            ledger.commit_segment(changed)

    def test_gap_terminalizes_prior_range_before_sparse_release(self):
        ledger = CustodyLedger(self.key)
        ledger.admit(self.c0)
        ledger.admit(self.c1)
        ledger.forward(self.c1)
        self.assertFalse(ledger.release_authorized(self.c1))
        ledger.commit_terminal(TerminalClaim(TerminalKind.GAP, self.key, (self.c0,), reason="durable_discard"))
        self.assertTrue(ledger.release_authorized(self.c1))

    def test_derived_prefix_never_crosses_gap(self):
        sparse = IntervalSet((Interval(2, 2, 320, 480),))
        self.assertIsNone(sparse.derived_contiguous_prefix(0, 0))
        self.assertEqual(sparse.derived_contiguous_prefix(2, 320), sparse.intervals[0])

    def test_stale_effect_and_deletion_generations_fail_closed(self):
        effect = ProviderEffectFence("effect")
        token = effect.prepare("owner")
        effect.invoke(token)
        effect.recovery_epoch(1, 1)
        with self.assertRaises(ProtocolV2Violation):
            effect.provider_ack(token)
        deletion = DeletionFence()
        generation = deletion.request()
        with self.assertRaises(ProtocolV2Violation):
            deletion.acknowledge_worker("worker", generation + 1)

    def test_schema_pins_uint64_domains_and_decimal_interval_strings(self):
        path = Path(__file__).resolve().parents[2] / "schema" / "protocol-v2.schema.json"
        with path.open(encoding="utf-8") as handle:
            schema = json.load(handle)
        self.assertEqual(schema["properties"]["captureGeneration"]["$ref"], "#/$defs/uint64Decimal")
        self.assertEqual(schema["$defs"]["uint64Decimal"]["maxLength"], 20)
        uint64_pattern = re.compile(schema["$defs"]["uint64Decimal"]["pattern"])
        self.assertIsNotNone(uint64_pattern.fullmatch(str(2**64 - 1)))
        self.assertIsNone(uint64_pattern.fullmatch(str(2**64)))
        for name in ("firstSequence", "lastSequenceInclusive", "firstSample", "lastSampleExclusive"):
            self.assertEqual(schema["$defs"]["interval"]["properties"][name]["maxLength"], 20)


if __name__ == "__main__":
    unittest.main()

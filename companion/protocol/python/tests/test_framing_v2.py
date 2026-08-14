import hashlib
import struct
import unittest

from tars_phase2.framing import (
    MAX_AUDIO_METADATA_BYTES,
    RetryCommitmentLedger,
    audio_event_id,
    audio_metadata,
    encode_audio_frame,
    parse_audio_frame,
    parse_control_event,
    retry_commitment,
    verify_retry_commitment,
)
from tars_phase2.model import AudioChunkV2, ProtocolV2Violation, Source, StreamKey, canonical_json_bytes


class ProtocolV2FramingTests(unittest.TestCase):
    def setUp(self):
        self.key = StreamKey("session-v2", "stream-mic", 4, Source.MICROPHONE)
        self.payload = bytes((index * 17 + 3) % 256 for index in range(320))
        self.chunk = AudioChunkV2(self.key, 0, 0, 160, 8_000, 1, 20, self.payload)
        self.frame = encode_audio_frame(self.chunk)

    def mutate_metadata(self, **changes):
        metadata = audio_metadata(self.chunk)
        metadata.update(changes)
        encoded = canonical_json_bytes(metadata)
        return struct.pack(">I", len(encoded)) + encoded + self.payload

    def test_canonical_audio_frame_round_trip_and_typed_identity(self):
        parsed = parse_audio_frame(self.frame)
        self.assertEqual(parsed.chunk, self.chunk)
        self.assertEqual(parsed.event_id, audio_event_id(self.key, 0, 0, 160))
        self.assertEqual(parsed.canonical_metadata, canonical_json_bytes(audio_metadata(self.chunk)))
        self.assertLessEqual(len(parsed.canonical_metadata), MAX_AUDIO_METADATA_BYTES)
        self.assertEqual(hashlib.sha256(parsed.chunk.payload).hexdigest(), self.chunk.payload_digest_sha256)

    def test_frame_rejects_prefix_truncation_oversize_length_and_payload_mismatch(self):
        cases = (
            b"",
            b"\0\0\0",
            struct.pack(">I", 4_097),
            struct.pack(">I", 50) + b"{}",
            self.frame[:-1],
            self.frame + b"extra",
        )
        for frame in cases:
            with self.assertRaises(ProtocolV2Violation):
                parse_audio_frame(frame)

    def test_frame_rejects_digest_identity_numeric_and_field_conflicts(self):
        bad_digest = bytearray(self.frame)
        bad_digest[-1] ^= 1
        cases = (
            bytes(bad_digest),
            self.mutate_metadata(eventId="aevt_" + "0" * 64),
            self.mutate_metadata(sequence="00"),
            self.mutate_metadata(captureGeneration="18446744073709551616"),
            self.mutate_metadata(protocolVersion=1),
            self.mutate_metadata(encoding="pcm_f32le"),
            self.mutate_metadata(extra="forbidden"),
        )
        for frame in cases:
            with self.assertRaises(ProtocolV2Violation):
                parse_audio_frame(frame)

    def test_frame_rejects_noncanonical_metadata_before_mutation(self):
        canonical = canonical_json_bytes(audio_metadata(self.chunk))
        noncanonical = b"{ " + canonical[1:]
        frame = struct.pack(">I", len(noncanonical)) + noncanonical + self.payload
        with self.assertRaises(ProtocolV2Violation):
            parse_audio_frame(frame)

    def test_retry_commitment_survives_restart_and_rejects_changed_content(self):
        session_key = bytes(range(32))
        ledger = RetryCommitmentLedger("session-v2", session_key)
        self.assertTrue(ledger.admit(self.frame))
        self.assertFalse(ledger.admit(self.frame))
        restarted = RetryCommitmentLedger("session-v2", session_key, ledger.snapshot())
        self.assertFalse(restarted.admit(self.frame))

        changed_payload = bytes((value ^ 0x5A) for value in self.payload)
        changed_chunk = AudioChunkV2(self.key, 0, 0, 160, 8_000, 1, 20, changed_payload)
        with self.assertRaises(ProtocolV2Violation):
            restarted.admit(encode_audio_frame(changed_chunk))

    def test_retry_hmac_is_exact_and_constant_time_verifiable(self):
        parsed = parse_audio_frame(self.frame)
        key = bytes(range(32))
        commitment = retry_commitment(key, parsed.canonical_metadata, parsed.chunk.payload)
        self.assertEqual(commitment.hex(), "4a8d1b9605f776c966ac0d62c5a459ead0922a026c521f9e95accce7f069e4c2")
        self.assertTrue(verify_retry_commitment(key, parsed.canonical_metadata, parsed.chunk.payload, commitment))
        self.assertFalse(verify_retry_commitment(bytes(reversed(key)), parsed.canonical_metadata, parsed.chunk.payload, commitment))
        with self.assertRaises(ProtocolV2Violation):
            retry_commitment(key, canonical_json_bytes({"eventType": "capture.pause"}), b"")

    def test_control_event_requires_canonical_exact_fields(self):
        value = {
            "protocolVersion": 2,
            "eventType": "capture.pause",
            "sessionId": "session-v2",
            "streamId": "stream-mic",
            "source": "microphone",
            "captureGeneration": "4",
            "eventId": "command-1",
        }
        encoded = canonical_json_bytes(value)
        self.assertEqual(parse_control_event(encoded, expected_event_type="capture.pause"), value)
        value["extra"] = "forbidden"
        with self.assertRaises(ProtocolV2Violation):
            parse_control_event(canonical_json_bytes(value), expected_event_type="capture.pause")


if __name__ == "__main__":
    unittest.main()

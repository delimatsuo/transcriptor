"""Canonical protocol-v2 framing and retry commitments.

The module is pure and memory-only. It deliberately contains no transport,
filesystem, provider, credential-store, or device integration.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import struct
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from .model import (
    MAX_AUDIO_PAYLOAD_BYTES,
    MAX_CONTROL_BYTES,
    MAX_SAFE_JSON_INTEGER,
    MAX_U64,
    AudioChunkV2,
    ProtocolV2Violation,
    Source,
    StreamKey,
    canonical_json_bytes,
    parse_canonical_json_bytes,
)


PROTOCOL_VERSION = 2
WEBSOCKET_SUBPROTOCOL = "tars.capture.v2"
MAX_AUDIO_METADATA_BYTES = 4_096
MAX_AUDIO_FRAME_BYTES = 68_100
MIN_SESSION_KEY_BYTES = 32
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_DECIMAL_U64_RE = re.compile(r"^(0|[1-9][0-9]{0,19})$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

BASE_FIELDS = frozenset(
    {
        "protocolVersion",
        "eventType",
        "sessionId",
        "streamId",
        "source",
        "captureGeneration",
        "eventId",
    }
)
AUDIO_FIELDS = BASE_FIELDS | frozenset(
    {
        "sequence",
        "firstSample",
        "lastSampleExclusive",
        "sampleRateHertz",
        "channelCount",
        "durationMs",
        "payloadBytes",
        "payloadDigestSha256",
        "encoding",
    }
)


def _identifier(name: str, value: Any, *, event_type: bool = False) -> str:
    pattern = _EVENT_TYPE_RE if event_type else _IDENTIFIER_RE
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ProtocolV2Violation(f"{name} is not a valid protocol identifier")
    return value


def _safe_integer(name: str, value: Any, *, minimum: int = 0, maximum: int = MAX_SAFE_JSON_INTEGER) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ProtocolV2Violation(f"{name} is outside its checked integer domain")
    return value


def _decimal_u64(name: str, value: Any) -> int:
    if not isinstance(value, str) or not _DECIMAL_U64_RE.fullmatch(value):
        raise ProtocolV2Violation(f"{name} is not a canonical uint64 decimal string")
    parsed = int(value)
    if parsed > MAX_U64:
        raise ProtocolV2Violation(f"{name} exceeds uint64")
    return parsed


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str]) -> None:
    keys = frozenset(value)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise ProtocolV2Violation(f"metadata fields are not exact; missing={missing}, extra={extra}")


def _base_stream(metadata: Mapping[str, Any], expected_event_type: Optional[str] = None) -> tuple[StreamKey, str]:
    if metadata.get("protocolVersion") != PROTOCOL_VERSION:
        raise ProtocolV2Violation("unsupported protocol version")
    event_type = _identifier("eventType", metadata.get("eventType"), event_type=True)
    if expected_event_type is not None and event_type != expected_event_type:
        raise ProtocolV2Violation("unexpected event type")
    event_id = _identifier("eventId", metadata.get("eventId"))
    try:
        source = Source(metadata.get("source"))
    except (TypeError, ValueError) as exc:
        raise ProtocolV2Violation("source is invalid") from exc
    key = StreamKey(
        _identifier("sessionId", metadata.get("sessionId")),
        _identifier("streamId", metadata.get("streamId")),
        _decimal_u64("captureGeneration", metadata.get("captureGeneration")),
        source,
    )
    return key, event_id


def audio_event_id(key: StreamKey, sequence: int, first_sample: int, last_sample_exclusive: int) -> str:
    for name, value in (
        ("sequence", sequence),
        ("firstSample", first_sample),
        ("lastSampleExclusive", last_sample_exclusive),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_U64:
            raise ProtocolV2Violation(f"{name} exceeds uint64")
    if last_sample_exclusive <= first_sample:
        raise ProtocolV2Violation("audio event sample range is empty")
    fields = (
        "tars-audio-event-v2",
        key.session_id,
        key.stream_id,
        str(key.capture_generation),
        key.source.value,
        str(sequence),
        str(first_sample),
        str(last_sample_exclusive),
    )
    return "aevt_" + hashlib.sha256("\0".join(fields).encode("utf-8")).hexdigest()


def audio_metadata(chunk: AudioChunkV2) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "eventType": "audio.chunk",
        "sessionId": chunk.key.session_id,
        "streamId": chunk.key.stream_id,
        "source": chunk.key.source.value,
        "captureGeneration": str(chunk.key.capture_generation),
        "eventId": audio_event_id(chunk.key, chunk.sequence, chunk.first_sample, chunk.last_sample_exclusive),
        "sequence": str(chunk.sequence),
        "firstSample": str(chunk.first_sample),
        "lastSampleExclusive": str(chunk.last_sample_exclusive),
        "sampleRateHertz": chunk.sample_rate_hertz,
        "channelCount": chunk.channel_count,
        "durationMs": chunk.duration_ms,
        "payloadBytes": len(chunk.payload),
        "payloadDigestSha256": chunk.payload_digest_sha256,
        "encoding": "pcm_s16le",
    }


@dataclass(frozen=True)
class ParsedAudioFrame:
    chunk: AudioChunkV2
    event_id: str
    canonical_metadata: bytes


def encode_audio_frame(chunk: AudioChunkV2) -> bytes:
    metadata = canonical_json_bytes(audio_metadata(chunk))
    if len(metadata) > MAX_AUDIO_METADATA_BYTES:
        raise ProtocolV2Violation("audio metadata exceeds 4096 bytes")
    frame = struct.pack(">I", len(metadata)) + metadata + chunk.payload
    if len(frame) > MAX_AUDIO_FRAME_BYTES:
        raise ProtocolV2Violation("audio frame exceeds 68100 bytes")
    return frame


def parse_audio_frame(frame: bytes) -> ParsedAudioFrame:
    if not isinstance(frame, bytes) or len(frame) < 4 or len(frame) > MAX_AUDIO_FRAME_BYTES:
        raise ProtocolV2Violation("audio frame size is invalid")
    metadata_length = struct.unpack(">I", frame[:4])[0]
    if metadata_length == 0 or metadata_length > MAX_AUDIO_METADATA_BYTES:
        raise ProtocolV2Violation("declared audio metadata length is invalid")
    metadata_end = 4 + metadata_length
    if metadata_end > len(frame):
        raise ProtocolV2Violation("audio metadata is truncated")
    canonical_metadata = frame[4:metadata_end]
    metadata = parse_canonical_json_bytes(canonical_metadata)
    if not isinstance(metadata, Mapping):
        raise ProtocolV2Violation("audio metadata must be an object")
    _exact_fields(metadata, AUDIO_FIELDS)
    key, event_id = _base_stream(metadata, "audio.chunk")
    sequence = _decimal_u64("sequence", metadata["sequence"])
    first_sample = _decimal_u64("firstSample", metadata["firstSample"])
    last_sample_exclusive = _decimal_u64("lastSampleExclusive", metadata["lastSampleExclusive"])
    sample_rate = _safe_integer("sampleRateHertz", metadata["sampleRateHertz"])
    channel_count = _safe_integer("channelCount", metadata["channelCount"])
    duration_ms = _safe_integer("durationMs", metadata["durationMs"])
    payload_bytes = _safe_integer("payloadBytes", metadata["payloadBytes"], maximum=MAX_AUDIO_PAYLOAD_BYTES)
    digest = metadata["payloadDigestSha256"]
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ProtocolV2Violation("payload digest is invalid")
    if metadata["encoding"] != "pcm_s16le":
        raise ProtocolV2Violation("audio encoding is invalid")
    payload = frame[metadata_end:]
    if len(payload) != payload_bytes:
        raise ProtocolV2Violation("audio payload length does not match metadata")
    actual_digest = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual_digest, digest):
        raise ProtocolV2Violation("audio payload digest mismatch")
    chunk = AudioChunkV2(
        key,
        sequence,
        first_sample,
        last_sample_exclusive,
        sample_rate,
        channel_count,
        duration_ms,
        payload,
    )
    expected_event_id = audio_event_id(key, sequence, first_sample, last_sample_exclusive)
    if not hmac.compare_digest(event_id, expected_event_id):
        raise ProtocolV2Violation("typed audio event identity mismatch")
    return ParsedAudioFrame(chunk, event_id, canonical_metadata)


def parse_control_event(
    payload: bytes,
    *,
    expected_event_type: str,
    extra_fields: Iterable[str] = (),
) -> Mapping[str, Any]:
    if len(payload) > MAX_CONTROL_BYTES:
        raise ProtocolV2Violation("control event exceeds 65536 bytes")
    value = parse_canonical_json_bytes(payload)
    if not isinstance(value, Mapping):
        raise ProtocolV2Violation("control event must be an object")
    expected = BASE_FIELDS | frozenset(extra_fields)
    _exact_fields(value, expected)
    _base_stream(value, expected_event_type)
    return value


def retry_commitment(session_key: bytes, canonical_metadata: bytes, payload: bytes) -> bytes:
    if not isinstance(session_key, bytes) or len(session_key) < MIN_SESSION_KEY_BYTES:
        raise ProtocolV2Violation("session retry key must contain at least 256 bits")
    if not isinstance(canonical_metadata, bytes) or not isinstance(payload, bytes):
        raise ProtocolV2Violation("retry commitment inputs must be bytes")
    if len(canonical_metadata) > MAX_AUDIO_METADATA_BYTES or len(payload) > MAX_AUDIO_PAYLOAD_BYTES:
        raise ProtocolV2Violation("retry commitment input exceeds framing bounds")
    # Revalidate the complete canonical typed audio frame before deriving a
    # durable commitment; generic canonical control JSON is not audio authority.
    parse_audio_frame(struct.pack(">I", len(canonical_metadata)) + canonical_metadata + payload)
    message = (
        b"tars-retry-v2\0"
        + struct.pack(">I", len(canonical_metadata))
        + canonical_metadata
        + struct.pack(">I", len(payload))
        + payload
    )
    return hmac.digest(session_key, message, "sha256")


def verify_retry_commitment(
    session_key: bytes,
    canonical_metadata: bytes,
    payload: bytes,
    expected: bytes,
) -> bool:
    if not isinstance(expected, bytes) or len(expected) != hashlib.sha256().digest_size:
        return False
    return hmac.compare_digest(retry_commitment(session_key, canonical_metadata, payload), expected)


class RetryCommitmentLedger:
    """Session-scoped commitment ledger that can be reconstructed after restart."""

    def __init__(
        self,
        session_id: str,
        session_key: bytes,
        commitments: Optional[Mapping[str, bytes]] = None,
    ) -> None:
        self.session_id = _identifier("sessionId", session_id)
        if not isinstance(session_key, bytes) or len(session_key) < MIN_SESSION_KEY_BYTES:
            raise ProtocolV2Violation("session retry key must contain at least 256 bits")
        self._session_key = session_key
        self._commitments = dict(commitments or {})
        for event_id, commitment in self._commitments.items():
            _identifier("eventId", event_id)
            if not isinstance(commitment, bytes) or len(commitment) != hashlib.sha256().digest_size:
                raise ProtocolV2Violation("stored retry commitment is invalid")

    def admit(self, frame: bytes) -> bool:
        parsed = parse_audio_frame(frame)
        if parsed.chunk.key.session_id != self.session_id:
            raise ProtocolV2Violation("retry commitment session mismatch")
        commitment = retry_commitment(self._session_key, parsed.canonical_metadata, parsed.chunk.payload)
        existing = self._commitments.get(parsed.event_id)
        if existing is not None:
            if not hmac.compare_digest(existing, commitment):
                raise ProtocolV2Violation("retry event identity was reused with changed content")
            return False
        self._commitments[parsed.event_id] = commitment
        return True

    def snapshot(self) -> dict[str, bytes]:
        return dict(self._commitments)

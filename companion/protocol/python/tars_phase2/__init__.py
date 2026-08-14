"""Pure, memory-only protocol-v2 model used by the G2-A offline corridor."""

from .model import (
    AudioChunkV2,
    AtomicCoverage,
    CustodyLedger,
    Interval,
    IntervalSet,
    ProtocolV2Violation,
    Source,
    StreamKey,
    TerminalClaim,
    TranscriptSegment,
    canonical_json_bytes,
    terminal_coverage_id,
    transcript_segment_id,
)
from .simulator import DeletionFence, DeletionState, EffectState, ProviderEffectFence, QuotaLimits, TokenBucketQuota

__all__ = [
    "AudioChunkV2",
    "AtomicCoverage",
    "CustodyLedger",
    "Interval",
    "IntervalSet",
    "ProtocolV2Violation",
    "Source",
    "StreamKey",
    "TerminalClaim",
    "TranscriptSegment",
    "DeletionFence",
    "DeletionState",
    "EffectState",
    "ProviderEffectFence",
    "QuotaLimits",
    "TokenBucketQuota",
    "canonical_json_bytes",
    "terminal_coverage_id",
    "transcript_segment_id",
]

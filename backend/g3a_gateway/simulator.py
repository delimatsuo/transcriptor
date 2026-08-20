"""Deterministic orchestration for generated-byte G3A verification."""

from __future__ import annotations

from dataclasses import dataclass

from .admission import AdmissionController
from .authority import AuthorityStore
from .contracts import (
    ActorContext,
    AtomicRange,
    AudioFrame,
    Disclosure,
    EffectIntent,
    Segment,
    Source,
    TerminalClaim,
    TerminalOutcome,
)
from .coverage import CoverageLedger
from .deletion import DeletionCoordinator
from .effects import EffectLedger
from .observability import ContentFreeLog, RollbackGuard
from .transport import TransportLedger


@dataclass(frozen=True)
class SessionHandle:
    context: ActorContext
    owner_id: str
    runtime_epoch: int


class G3ASimulator:
    """A complete pure state path with no network or provider capability."""

    def __init__(self) -> None:
        self.authority = AuthorityStore()
        self.admission = AdmissionController(self.authority)
        self.transport = TransportLedger(self.admission)
        self.effects = EffectLedger()
        self.coverage = CoverageLedger()
        self.deletion = DeletionCoordinator()
        self.log = ContentFreeLog()
        self.rollback = RollbackGuard()

    def open_session(self, now_ms: int = 1_000) -> SessionHandle:
        token = self.authority.issue_enrollment("actor-1", "org-1", now_ms)
        disclosure = Disclosure("notice-v2", "actor-1", "org-1", "session-1", now_ms, "consent")
        context = self.authority.open_context(
            token,
            session_id="session-1",
            stream_id="stream-1",
            capture_generation=1,
            disclosure=disclosure,
            now_ms=now_ms + 1,
        )
        lease = self.authority.acquire_lease(context.session_id, "owner-1", context.capture_generation)
        self.deletion.register_worker("worker-1")
        self.log.record("session_admitted", sources=2)
        return SessionHandle(context, lease.owner_id, lease.runtime_epoch)

    def capture(self, handle: SessionHandle, sequence: int, payload: bytes, now_ms: int) -> AudioFrame:
        frame = AudioFrame(
            context=handle.context,
            event_id=f"event-{sequence}",
            sequence=sequence,
            first_sample=sequence * 800,
            last_sample_exclusive=(sequence + 1) * 800,
            sample_rate=8_000,
            channels=1,
            captured_at_ms=now_ms,
            payload=payload,
            metadata_bytes=64,
        )
        self.transport.capture(frame, owner_id=handle.owner_id, runtime_epoch=handle.runtime_epoch, now_ms=now_ms)
        self.coverage.admit_range(AtomicRange(sequence, sequence))
        return frame

    def forward(self, last_sequence_inclusive: int) -> None:
        self.transport.journal_forward(0, last_sequence_inclusive)
        self.log.record("forwarded", ranges=1)

    def terminalize(self, sequence: int, text_digest: str = "fixture-text") -> None:
        atomic = AtomicRange(sequence, sequence)
        segment = Segment(f"segment-{sequence}", sequence * 800, (sequence + 1) * 800, text_digest)
        self.coverage.terminalize(TerminalClaim(atomic, TerminalOutcome.TRANSCRIPT, (segment,)))

    def prepare_effect(self, sequence: int) -> EffectIntent:
        intent = self.effects.prepare(AtomicRange(sequence, sequence), "owner-1", 1)
        self.deletion.register_effect(intent.intent_id)
        return intent

    def delete(self) -> None:
        generation = self.deletion.request()
        self.admission.revoke_session("session-1")
        self.deletion.acknowledge_worker("worker-1", generation)
        for intent in tuple(self.effects._intents.values()):
            if intent.state.value in ("prepared", "invoking"):
                self.effects.require_quiescence(intent.intent_id, intent.owner)
                self.effects.acknowledge_quiescence(intent.intent_id, intent.owner)
            self.deletion.acknowledge_effect(
                intent.intent_id,
                generation,
                quiesced=self.effects.all_quiesced(),
            )
        self.deletion.progress()
        self.deletion.absence_pass(generation, set())
        self.deletion.absence_pass(generation, set())
        self.log.record("deleted", passes=2)

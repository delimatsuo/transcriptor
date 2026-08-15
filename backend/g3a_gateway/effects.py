"""Deterministic provider-effect seam with opaque owner/fence recovery."""

from __future__ import annotations

from dataclasses import replace

from .contracts import (
    AtomicRange,
    EffectIntent,
    EffectOwner,
    EffectState,
    FailureCode,
    GatewayError,
)


class EffectLedger:
    """Models provider effects without importing or invoking a provider."""

    def __init__(self) -> None:
        self._intents: dict[str, EffectIntent] = {}
        self._next_generation = 0

    def prepare(self, atomic_range: AtomicRange, owner_id: str, runtime_epoch: int) -> EffectIntent:
        self._next_generation += 1
        owner = EffectOwner(owner_id, runtime_epoch, self._next_generation)
        intent_id = f"effect-{self._next_generation}"
        intent = EffectIntent(intent_id, atomic_range, owner, EffectState.PREPARED)
        self._intents[intent_id] = intent
        return intent

    def get(self, intent_id: str) -> EffectIntent:
        try:
            return self._intents[intent_id]
        except KeyError as exc:
            raise GatewayError(FailureCode.CONFLICT) from exc

    def _require_owner(self, intent: EffectIntent, owner: EffectOwner) -> None:
        if intent.owner != owner:
            raise GatewayError(FailureCode.STALE_FENCE)

    def discard_before_prepare(self, atomic_range: AtomicRange) -> None:
        if any(intent.atomic_range == atomic_range for intent in self._intents.values()):
            raise GatewayError(FailureCode.CONFLICT)

    def begin_invocation(self, intent_id: str, owner: EffectOwner) -> EffectIntent:
        intent = self.get(intent_id)
        self._require_owner(intent, owner)
        if intent.state is not EffectState.PREPARED:
            raise GatewayError(FailureCode.CONFLICT)
        updated = replace(intent, state=EffectState.INVOKING)
        self._intents[intent_id] = updated
        return updated

    def provider_callback(self, intent_id: str, owner: EffectOwner, *, forwarded: bool) -> EffectIntent:
        intent = self.get(intent_id)
        self._require_owner(intent, owner)
        if intent.state is not EffectState.INVOKING:
            raise GatewayError(FailureCode.STALE_FENCE)
        updated = replace(intent, state=EffectState.FORWARDED if forwarded else EffectState.AMBIGUOUS)
        self._intents[intent_id] = updated
        return updated

    def mark_discarded(self, intent_id: str, owner: EffectOwner) -> EffectIntent:
        intent = self.get(intent_id)
        self._require_owner(intent, owner)
        if intent.state not in (EffectState.PREPARED, EffectState.INVOKING):
            raise GatewayError(FailureCode.CONFLICT)
        updated = replace(intent, state=EffectState.DISCARDED)
        self._intents[intent_id] = updated
        return updated

    def require_quiescence(self, intent_id: str, owner: EffectOwner) -> EffectIntent:
        intent = self.get(intent_id)
        self._require_owner(intent, owner)
        if intent.state in (EffectState.PREPARED, EffectState.INVOKING):
            updated = replace(intent, state=EffectState.EFFECT_QUIESCENCE_REQUIRED)
            self._intents[intent_id] = updated
            return updated
        return intent

    def acknowledge_quiescence(self, intent_id: str, owner: EffectOwner) -> EffectIntent:
        intent = self.get(intent_id)
        self._require_owner(intent, owner)
        if intent.state not in (EffectState.EFFECT_QUIESCENCE_REQUIRED, EffectState.DISCARDED):
            raise GatewayError(FailureCode.CONFLICT)
        updated = replace(intent, state=EffectState.QUIESCED)
        self._intents[intent_id] = updated
        return updated

    def all_quiesced(self) -> bool:
        return all(
            intent.state
            in (EffectState.FORWARDED, EffectState.AMBIGUOUS, EffectState.DISCARDED, EffectState.QUIESCED)
            for intent in self._intents.values()
        )

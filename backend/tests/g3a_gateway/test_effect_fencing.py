from __future__ import annotations

import pytest

from backend.g3a_gateway.contracts import AtomicRange, EffectState, FailureCode, GatewayError
from backend.g3a_gateway.effects import EffectLedger


def test_stale_owner_cannot_invoke_or_accept_callback() -> None:
    ledger = EffectLedger()
    intent = ledger.prepare(AtomicRange(0, 0), "owner-a", 1)
    with pytest.raises(GatewayError) as stale:
        ledger.begin_invocation(intent.intent_id, intent.owner.__class__("owner-b", 1, 1))
    assert stale.value.code is FailureCode.STALE_FENCE
    invoking = ledger.begin_invocation(intent.intent_id, intent.owner)
    assert invoking.state is EffectState.INVOKING
    with pytest.raises(GatewayError) as callback:
        ledger.provider_callback(intent.intent_id, intent.owner.__class__("owner-a", 2, 1), forwarded=True)
    assert callback.value.code is FailureCode.STALE_FENCE


def test_effect_quiescence_is_non_success_until_acknowledged() -> None:
    ledger = EffectLedger()
    intent = ledger.prepare(AtomicRange(1, 1), "owner", 1)
    required = ledger.require_quiescence(intent.intent_id, intent.owner)
    assert required.state is EffectState.EFFECT_QUIESCENCE_REQUIRED
    assert not ledger.all_quiesced()
    acknowledged = ledger.acknowledge_quiescence(intent.intent_id, intent.owner)
    assert acknowledged.state is EffectState.QUIESCED
    assert ledger.all_quiesced()


def test_discard_cas_cannot_claim_an_existing_effect() -> None:
    ledger = EffectLedger()
    ledger.prepare(AtomicRange(2, 2), "owner", 1)
    with pytest.raises(GatewayError) as exc:
        ledger.discard_before_prepare(AtomicRange(2, 2))
    assert exc.value.code is FailureCode.CONFLICT


def test_discard_cas_blocks_a_later_prepare_and_cannot_claim_invoking_effect() -> None:
    ledger = EffectLedger()
    discarded = AtomicRange(3, 3)
    ledger.discard_before_prepare(discarded)
    with pytest.raises(GatewayError) as resurrect:
        ledger.prepare(discarded, "owner", 1)
    assert resurrect.value.code is FailureCode.CONFLICT

    intent = ledger.prepare(AtomicRange(4, 4), "owner", 1)
    ledger.begin_invocation(intent.intent_id, intent.owner)
    with pytest.raises(GatewayError) as pending:
        ledger.mark_discarded(intent.intent_id, intent.owner)
    assert pending.value.code is FailureCode.QUIESCENCE_REQUIRED

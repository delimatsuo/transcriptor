from __future__ import annotations

import pytest

from backend.g3a_gateway.authority import AuthorityStore
from backend.g3a_gateway.contracts import Disclosure, FailureCode, GatewayError


def _disclosure(session: str = "s1") -> Disclosure:
    return Disclosure("notice-v2", "actor", "org", session, 100, "consent")


def test_context_is_server_derived_and_disclosure_bound() -> None:
    store = AuthorityStore()
    token = store.issue_enrollment("actor", "org", 100)
    context = store.open_context(
        token,
        session_id="s1",
        stream_id="stream",
        capture_generation=7,
        disclosure=_disclosure(),
        now_ms=101,
    )
    assert context.actor_id == "actor"
    assert context.organization_id == "org"
    assert context.capture_generation == 7


def test_wrong_tenant_and_expired_enrollment_fail_closed() -> None:
    store = AuthorityStore()
    token = store.issue_enrollment("actor", "org", 100, ttl_ms=10)
    with pytest.raises(GatewayError) as wrong_tenant:
        store.open_context(
            token,
            session_id="s1",
            stream_id="stream",
            capture_generation=1,
            disclosure=Disclosure("notice-v2", "actor", "other", "s1", 100, "consent"),
            now_ms=101,
        )
    assert wrong_tenant.value.code is FailureCode.DISCLOSURE_REQUIRED
    with pytest.raises(GatewayError) as expired:
        store.open_context(
            token,
            session_id="s1",
            stream_id="stream",
            capture_generation=1,
            disclosure=_disclosure(),
            now_ms=110,
        )
    assert expired.value.code is FailureCode.UNAUTHENTICATED


def test_lease_replacement_fences_old_owner() -> None:
    store = AuthorityStore()
    first = store.acquire_lease("s1", "owner-a", 1)
    second = store.acquire_lease("s1", "owner-b", 1)
    assert second.runtime_epoch > first.runtime_epoch
    with pytest.raises(GatewayError) as exc:
        store.require_lease("s1", "owner-a", first.runtime_epoch)
    assert exc.value.code is FailureCode.STALE_FENCE

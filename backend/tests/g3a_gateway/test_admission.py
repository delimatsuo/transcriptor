from __future__ import annotations

import pytest

from backend.g3a_gateway.admission import AdmissionController
from backend.g3a_gateway.authority import AuthorityStore
from backend.g3a_gateway.contracts import AudioFrame, Disclosure, FailureCode, GatewayError, QuotaLimits, Source


def _frame(sequence: int = 0, payload: bytes = b"x" * 1600) -> AudioFrame:
    authority = AuthorityStore()
    token = authority.issue_enrollment("actor", "org", 100)
    context = authority.open_context(
        token,
        session_id="s1",
        stream_id="stream",
        capture_generation=1,
        disclosure=Disclosure("notice-v2", "actor", "org", "s1", 100, "consent"),
        now_ms=101,
    )
    return AudioFrame(context, f"event-{sequence}", sequence, sequence * 800, (sequence + 1) * 800, 8_000, 1, 1000, payload, 32)


def test_rejects_before_reservation_when_burst_is_exceeded() -> None:
    authority = AuthorityStore()
    token = authority.issue_enrollment("actor", "org", 100)
    context = authority.open_context(
        token,
        session_id="s1",
        stream_id="stream",
        capture_generation=1,
        disclosure=Disclosure("notice-v2", "actor", "org", "s1", 100, "consent"),
        now_ms=101,
    )
    lease = authority.acquire_lease("s1", "owner", 1)
    admission = AdmissionController(authority, QuotaLimits(audio_event_burst=1))
    frame = AudioFrame(context, "event", 0, 0, 800, 8_000, 1, 1000, b"x" * 1600, 32)
    admission.admit(frame, owner_id=lease.owner_id, runtime_epoch=lease.runtime_epoch, now_ms=1000)
    with pytest.raises(GatewayError) as exc:
        admission.admit(frame, owner_id=lease.owner_id, runtime_epoch=lease.runtime_epoch, now_ms=1000)
    assert exc.value.code is FailureCode.QUOTA_EXCEEDED
    assert admission.quotas._resident_bytes == 1600


def test_revocation_blocks_new_audio() -> None:
    authority = AuthorityStore()
    token = authority.issue_enrollment("actor", "org", 100)
    context = authority.open_context(
        token,
        session_id="s1",
        stream_id="stream",
        capture_generation=1,
        disclosure=Disclosure("notice-v2", "actor", "org", "s1", 100, "consent"),
        now_ms=101,
    )
    lease = authority.acquire_lease("s1", "owner", 1)
    admission = AdmissionController(authority)
    admission.revoke_session("s1")
    frame = AudioFrame(context, "event", 0, 0, 800, 8_000, 1, 1000, b"x", 1)
    with pytest.raises(GatewayError) as exc:
        admission.admit(frame, owner_id=lease.owner_id, runtime_epoch=lease.runtime_epoch, now_ms=1000)
    assert exc.value.code is FailureCode.REVOKED


def test_audio_buckets_are_bound_to_source_identity() -> None:
    authority = AuthorityStore()
    token = authority.issue_enrollment("actor", "org", 100)
    context = authority.open_context(
        token,
        session_id="s1",
        stream_id="stream",
        capture_generation=1,
        disclosure=Disclosure("notice-v2", "actor", "org", "s1", 100, "consent"),
        now_ms=101,
    )
    lease = authority.acquire_lease("s1", "owner", 1)
    admission = AdmissionController(authority, QuotaLimits(audio_event_burst=1))
    microphone = AudioFrame(
        context, "mic-0", 0, 0, 800, 8_000, 1, 1000, b"m" * 1600, 32, Source.MICROPHONE
    )
    system_audio = AudioFrame(
        context, "system-0", 0, 0, 800, 8_000, 1, 1000, b"s" * 1600, 32, Source.SYSTEM_AUDIO
    )
    admission.admit(microphone, owner_id=lease.owner_id, runtime_epoch=lease.runtime_epoch, now_ms=1000)
    admission.admit(system_audio, owner_id=lease.owner_id, runtime_epoch=lease.runtime_epoch, now_ms=1000)

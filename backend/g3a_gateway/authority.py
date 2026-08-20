"""Server-derived authority and fenced lease state for G3A."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .contracts import ActorContext, Disclosure, FailureCode, GatewayError, Lease


@dataclass(frozen=True)
class Enrollment:
    token_digest: str
    actor_id: str
    organization_id: str
    issued_at_ms: int
    expires_at_ms: int
    revoked: bool = False


class AuthorityStore:
    """Pure authority store; token material never leaves this object."""

    def __init__(self) -> None:
        self._enrollments: dict[str, Enrollment] = {}
        self._leases: dict[str, Lease] = {}
        self._epochs: dict[str, int] = {}

    @staticmethod
    def _digest(actor_id: str, organization_id: str, issued_at_ms: int) -> str:
        return sha256(f"{actor_id}\0{organization_id}\0{issued_at_ms}".encode()).hexdigest()

    def issue_enrollment(
        self,
        actor_id: str,
        organization_id: str,
        issued_at_ms: int,
        ttl_ms: int = 60_000,
    ) -> str:
        if not actor_id or not organization_id or ttl_ms <= 0:
            raise GatewayError(FailureCode.FORBIDDEN)
        digest = self._digest(actor_id, organization_id, issued_at_ms)
        self._enrollments[digest] = Enrollment(
            digest, actor_id, organization_id, issued_at_ms, issued_at_ms + ttl_ms
        )
        return digest

    def revoke_enrollment(self, token_digest: str) -> None:
        enrollment = self._enrollments.get(token_digest)
        if enrollment is None:
            raise GatewayError(FailureCode.FORBIDDEN)
        self._enrollments[token_digest] = Enrollment(**{**enrollment.__dict__, "revoked": True})

    def open_context(
        self,
        token_digest: str,
        *,
        session_id: str,
        stream_id: str,
        capture_generation: int,
        disclosure: Disclosure,
        now_ms: int,
    ) -> ActorContext:
        enrollment = self._enrollments.get(token_digest)
        if enrollment is None or enrollment.revoked or now_ms >= enrollment.expires_at_ms:
            raise GatewayError(FailureCode.UNAUTHENTICATED)
        if (
            disclosure.actor_id != enrollment.actor_id
            or disclosure.organization_id != enrollment.organization_id
            or disclosure.session_id != session_id
            or not disclosure.notice_version
            or not disclosure.legal_basis
            or disclosure.acknowledged_at_ms > now_ms
        ):
            raise GatewayError(FailureCode.DISCLOSURE_REQUIRED)
        return ActorContext(
            actor_id=enrollment.actor_id,
            organization_id=enrollment.organization_id,
            session_id=session_id,
            stream_id=stream_id,
            capture_generation=capture_generation,
            enrollment_digest=token_digest,
            disclosure=disclosure,
        )

    def acquire_lease(self, session_id: str, owner_id: str, capture_generation: int) -> Lease:
        previous = self._leases.get(session_id)
        epoch = self._epochs.get(session_id, 0) + 1
        self._epochs[session_id] = epoch
        if previous is not None:
            self._leases[session_id] = Lease(
                previous.owner_id,
                previous.runtime_epoch,
                previous.capture_generation,
                revoked=True,
            )
        lease = Lease(owner_id, epoch, capture_generation)
        self._leases[session_id] = lease
        return lease

    def current_lease(self, session_id: str) -> Lease | None:
        return self._leases.get(session_id)

    def require_lease(self, session_id: str, owner_id: str, runtime_epoch: int) -> Lease:
        lease = self._leases.get(session_id)
        if (
            lease is None
            or lease.revoked
            or lease.owner_id != owner_id
            or lease.runtime_epoch != runtime_epoch
        ):
            raise GatewayError(FailureCode.STALE_FENCE)
        return lease

    def revoke_lease(self, session_id: str) -> None:
        lease = self._leases.get(session_id)
        if lease is not None:
            self._leases[session_id] = Lease(
                lease.owner_id, lease.runtime_epoch, lease.capture_generation, revoked=True
            )

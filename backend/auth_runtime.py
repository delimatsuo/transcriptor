"""Process-local authorization safety gate for the Task 08 canary.

This is intentionally not durable provider revocation.  It only protects the
single application instance between signed assertion admission and socket
shutdown; deployed IAP/Identity Platform remains the provider authority.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class ConnectionLease:
    principal_uid: str
    principal_auth_time: int
    connection_id: str
    _closed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _released: bool = field(default=False, repr=False)
    _on_close: Callable[[], Any] | None = field(default=None, repr=False)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def event(self) -> asyncio.Event:
        return self._closed

    def signal(self) -> bool:
        if self._closed.is_set():
            return False
        self._closed.set()
        callback = self._on_close
        if callback is not None:
            try:
                result = callback()
                if inspect.isawaitable(result):
                    asyncio.create_task(result)
            except Exception:
                pass
        return True

    def close(self) -> bool:
        return self.signal()


class AuthRuntimeGate:
    """Monotonic kill/revocation and browser-connection lease registry."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._kill_latched = False
        self._principal_cutoffs: dict[str, int] = {}
        self._leases: dict[str, ConnectionLease] = {}
        # Admission operations span an awaited durable write (session
        # creation).  They are distinct from sockets so a kill/revocation can
        # close the in-flight operation before it publishes business state.
        self._operation_leases: dict[str, ConnectionLease] = {}
        self._tickets: dict[str, tuple[str, int]] = {}
        self._stream_keys: dict[str, str] = {}
        self._active_business_sessions = 0
        self._active_provider_operations = 0
        self._counter = 0

    @property
    def kill_latched(self) -> bool:
        return self._kill_latched

    @property
    def killed(self) -> bool:
        return self._kill_latched

    @staticmethod
    def _principal_values(uid: Any, auth_time: int | None = None) -> tuple[str, int]:
        if not isinstance(uid, str):
            principal = uid
            uid = getattr(principal, "uid", "")
            if auth_time is None:
                auth_time = getattr(principal, "auth_time", None)
        if (
            not isinstance(uid, str)
            or not uid
            or not isinstance(auth_time, int)
            or isinstance(auth_time, bool)
        ):
            raise ValueError("invalid principal")
        return uid, auth_time

    def is_principal_admissible(self, uid: str | Any, auth_time: int | None = None) -> bool:
        uid, auth_time = self._principal_values(uid, auth_time)
        return not self._kill_latched and self.is_principal_current(uid, auth_time)

    def is_principal_current(self, uid: str | Any, auth_time: int | None = None) -> bool:
        """Check the per-principal cutoff without consulting the global kill."""
        uid, auth_time = self._principal_values(uid, auth_time)
        cutoff = self._principal_cutoffs.get(uid)
        return cutoff is None or auth_time > cutoff

    def is_uid_current(self, uid: str | Any) -> bool:
        """Check whether a uid has a logout cutoff without requiring auth_time."""
        if not isinstance(uid, str):
            uid = str(getattr(uid, "uid", ""))
        if not uid:
            return False
        return uid not in self._principal_cutoffs

    def admit_principal(self, uid: str | Any, auth_time: int | None = None) -> bool:
        return self.is_principal_admissible(uid, auth_time)

    def revoke_principal(self, uid: str | Any, auth_time: int | None = None) -> bool:
        """Revoke this and all older validated sessions for one principal."""
        uid, auth_time = self._principal_values(uid, auth_time)
        previous = self._principal_cutoffs.get(uid)
        changed = previous is None or auth_time > previous
        if changed:
            self._principal_cutoffs[uid] = auth_time
        for lease in list(self._leases.values()):
            if lease.principal_uid == uid and lease.principal_auth_time <= auth_time:
                lease.signal()
        for lease in list(self._operation_leases.values()):
            if lease.principal_uid == uid and lease.principal_auth_time <= auth_time:
                lease.signal()
        for ticket, (ticket_uid, ticket_auth_time) in list(self._tickets.items()):
            if ticket_uid == uid and ticket_auth_time <= auth_time:
                self._tickets.pop(ticket, None)
        return changed

    def revoke(self, uid: str, auth_time: int) -> bool:
        return self.revoke_principal(uid, auth_time)

    def register_connection(
        self,
        uid: str | Any,
        auth_time: int | None = None,
        *,
        on_close: Callable[[], Any] | None = None,
    ) -> ConnectionLease | None:
        uid, auth_time = self._principal_values(uid, auth_time)
        if not self.is_principal_admissible(uid, auth_time):
            return None
        self._counter += 1
        connection_id = f"connection-{self._counter}"
        lease = ConnectionLease(uid, auth_time, connection_id, _on_close=on_close)
        self._leases[connection_id] = lease
        return lease

    def register_browser_connection(self, principal: Any) -> ConnectionLease | None:
        return self.register_connection(principal)

    def release_connection(self, lease: ConnectionLease | str | None) -> bool:
        if lease is None:
            return False
        connection_id = lease if isinstance(lease, str) else lease.connection_id
        existing = self._leases.pop(connection_id, None)
        if existing is None:
            return False
        existing._released = True
        existing.signal()
        return True

    def register_operation(
        self,
        uid: str | Any,
        auth_time: int | None = None,
    ) -> ConnectionLease | None:
        """Reserve an admission operation until its awaited side effects settle."""
        uid, auth_time = self._principal_values(uid, auth_time)
        if not self.is_principal_admissible(uid, auth_time):
            return None
        self._counter += 1
        operation_id = f"operation-{self._counter}"
        lease = ConnectionLease(uid, auth_time, operation_id)
        self._operation_leases[operation_id] = lease
        return lease

    def release_operation(self, lease: ConnectionLease | str | None) -> bool:
        """Release an admission operation in every success/failure path."""
        if lease is None:
            return False
        operation_id = lease if isinstance(lease, str) else lease.connection_id
        existing = self._operation_leases.pop(operation_id, None)
        if existing is None:
            return False
        existing._released = True
        existing.signal()
        return True

    # Explicit aliases make the admission lease seam easy to discover for
    # route code and offline race tests without exposing the internal store.
    register_admission = register_operation
    release_admission = release_operation

    def register_lease(self, uid: str, auth_time: int) -> ConnectionLease | None:
        return self.register_connection(uid, auth_time)

    def signal_principal(self, uid: str | Any, auth_time: int | None = None) -> int:
        uid, auth_time = self._principal_values(uid, auth_time)
        count = 0
        for lease in list(self._leases.values()):
            if lease.principal_uid == uid and lease.principal_auth_time <= auth_time:
                count += int(lease.signal())
        return count

    def kill(self) -> bool:
        """Latch closed first, then signal every lease and revoke capabilities."""
        already = self._kill_latched
        self._kill_latched = True
        self._tickets.clear()
        self._stream_keys.clear()
        for lease in list(self._leases.values()):
            lease.signal()
        for lease in list(self._operation_leases.values()):
            lease.signal()
        return not already

    def activate_kill_switch(self) -> bool:
        return self.kill()

    def register_ticket(self, token: str, uid: str | Any, auth_time: int | None = None) -> bool:
        uid, auth_time = self._principal_values(uid, auth_time)
        if not self.is_principal_admissible(uid, auth_time):
            return False
        self._tickets[token] = (uid, auth_time)
        return True

    def consume_ticket(self, token: str) -> None:
        self._tickets.pop(token, None)

    def revoke_tickets(self, uid: str | None = None) -> int:
        before = len(self._tickets)
        if uid is None:
            self._tickets.clear()
        else:
            for token, value in list(self._tickets.items()):
                if value[0] == uid:
                    self._tickets.pop(token, None)
        return before - len(self._tickets)

    def register_stream_key(self, session_id: str, uid: str | Any) -> bool:
        if not isinstance(uid, str):
            uid = str(getattr(uid, "uid", ""))
        if self._kill_latched:
            return False
        self._stream_keys[session_id] = uid
        return True

    def revoke_stream_keys(self, uid: str | None = None) -> int:
        before = len(self._stream_keys)
        if uid is None:
            self._stream_keys.clear()
        else:
            for session_id, owner in list(self._stream_keys.items()):
                if owner == uid:
                    self._stream_keys.pop(session_id, None)
        return before - len(self._stream_keys)

    def consume_stream_key(self, session_id: str) -> bool:
        return self._stream_keys.pop(session_id, None) is not None

    def set_active_business_sessions(self, count: int) -> None:
        self._active_business_sessions = max(0, int(count))

    def set_active_provider_operations(self, count: int) -> None:
        """Keep count-only readiness accounting in lockstep with provider work."""
        self._active_provider_operations = max(0, int(count))

    def counts(self) -> dict[str, int]:
        return {
            "active_business_sessions": self._active_business_sessions,
            "registered_browser_connections": len(self._leases),
            "outstanding_browser_tickets": len(self._tickets),
            "active_stream_keys": len(self._stream_keys),
            "active_provider_operations": self._active_provider_operations,
        }

    def reset_for_tests(self) -> None:
        """Clear process-local state for an isolated offline test fixture."""
        self._kill_latched = False
        self._principal_cutoffs.clear()
        self._leases.clear()
        self._operation_leases.clear()
        self._tickets.clear()
        self._stream_keys.clear()
        self._active_business_sessions = 0
        self._active_provider_operations = 0

    @property
    def live_connection_count(self) -> int:
        return len(self._leases)

    @property
    def active_browser_connections(self) -> int:
        return len(self._leases)

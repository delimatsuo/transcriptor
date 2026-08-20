"""Content-free diagnostics, kill control, and rollback state."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import Diagnostic, FailureCode, GatewayError


@dataclass(frozen=True)
class KillSwitch:
    active: bool = False
    reason_code: str | None = None


class ContentFreeLog:
    def __init__(self) -> None:
        self._events: list[Diagnostic] = []
        self._kill = KillSwitch()

    @property
    def events(self) -> tuple[Diagnostic, ...]:
        return tuple(self._events)

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill

    def record(self, name: str, **counters: int) -> None:
        if not name or any(not isinstance(value, int) for value in counters.values()):
            raise GatewayError(FailureCode.CONFLICT)
        self._events.append(Diagnostic(name, dict(counters)))

    def activate_kill_switch(self, reason_code: str) -> None:
        if not reason_code or any(character in reason_code for character in " \t\n"):
            raise GatewayError(FailureCode.CONFLICT)
        self._kill = KillSwitch(True, reason_code)

    def require_live(self) -> None:
        if self._kill.active:
            raise GatewayError(FailureCode.REVOKED)


class RollbackGuard:
    def __init__(self) -> None:
        self._rollback_requested = False

    @property
    def requested(self) -> bool:
        return self._rollback_requested

    def request(self) -> None:
        self._rollback_requested = True

    def require_not_requested(self) -> None:
        if self._rollback_requested:
            raise GatewayError(FailureCode.REVOKED)

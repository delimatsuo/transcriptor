"""Pure, memory-only G3A gateway state machine.

This package deliberately contains no framework, network, cloud, provider, or
filesystem integration. It models the protocol boundary for deterministic
generated-byte verification only.
"""

from .authority import AuthorityStore
from .contracts import (
    ActorContext,
    AudioFrame,
    CoverageState,
    Disclosure,
    EffectState,
    Source,
    TransportState,
)
from .simulator import G3ASimulator

__all__ = [
    "ActorContext",
    "AudioFrame",
    "AuthorityStore",
    "CoverageState",
    "Disclosure",
    "EffectState",
    "G3ASimulator",
    "Source",
    "TransportState",
]

#!/usr/bin/env python3
"""Offline policy and control core for the future Process Tap proof.

The module is intentionally importable without a backend, provider SDK,
LaunchServices, or audio APIs.  It owns the strict AF_UNIX framing/schema,
artifact policy, peer/session state machine, restart freshness rules, and the
single positive Process Tap predicate.  The credential is admitted exactly
once and is absent from all event/evidence schemas.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import plistlib
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from dataclasses import is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

PROTOCOL_VERSION = 2
MAX_PAYLOAD = 64 * 1024
_UINT64_MAX = (1 << 64) - 1
PROCESS_TAP = "process-tap"
SCREEN_CAPTURE_KIT = "screen-capture-kit"

COMMAND_FIELDS = frozenset(
    {"gateway", "launch_nonce", "session_id", "stream_key", "type", "version"}
)
SHUTDOWN_REQUEST_FIELDS = frozenset({"shutdown_binding", "shutdown_nonce", "type", "version"})
SHUTDOWN_ACK_FIELDS = frozenset({"shutdown_binding", "shutdown_nonce", "status", "type", "version"})
EVENT_BASE_FIELDS = frozenset(
    {
        "actual_engine",
        "attempt_id",
        "generation",
        "kind",
        "launch_nonce",
        "observer_binding",
        "requested_engine",
        "resolved_engine",
        "session_binding",
        "source_binding",
        "type",
        "version",
    }
)
EVENT_KINDS = frozenset({"activation", "health"})
HEALTH_FIELDS = frozenset(
    {"interruption", "kind", "overflowed", "permission", "route", "sleep"}
)


class LiveHarnessFailureCode(str, Enum):
    PERMISSION_DENIED = "permission-denied"
    CAPTURE_FAILED = "capture-failed"


HEALTH_FAILURE_CODES = frozenset(code.value for code in LiveHarnessFailureCode)
HEALTH_FAILURE_FIELDS = HEALTH_FIELDS | {"failure_code"}
HEALTH_PERMISSION_VALUES = frozenset({"unknown", "granted", "denied", "revoked"})
HEALTH_ROUTE_VALUES = frozenset({"unknown", "healthy", "unavailable", "ambiguous", "changed"})
HEALTH_INTERRUPTION_VALUES = frozenset({"clear", "interrupted"})
HEALTH_SLEEP_VALUES = frozenset({"awake", "sleeping", "woke"})
HEALTH_KIND_VALUES = frozenset({"idle", "ready", "running", "stopped", "failed"})
# This exact copy remains local UI/remediation copy only.  Failed health wire
# events carry the closed failure code below and never carry arbitrary text.
PERMISSION_DENIED_MESSAGE = (
    "O macOS negou a captura de áudio do sistema. Autorize o TarsCompanion em "
    "Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema e tente novamente."
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PRODUCER_LAUNCH_NONCE = _IDENTIFIER
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ATTEMPT_ID = re.compile(r"^at1_[0-9a-f]{32}$")
_LAUNCH_NONCE = re.compile(r"^ln1_[0-9a-f]{32}$")
_SESSION_BINDING = re.compile(r"^sb1_[0-9a-f]{64}$")
_SOURCE_BINDING = re.compile(r"^so1_[0-9a-f]{32}$")
_OBSERVER_BINDING = re.compile(r"^ob1_[0-9a-f]{32}$")
_PEER_FINGERPRINT = re.compile(r"^pb1_[0-9a-f]{64}$")
_STREAM_KEY = re.compile(r"^[A-Za-z0-9_-]{43}$")
_SHUTDOWN_NONCE = re.compile(r"^sn1_[0-9a-f]{32}$")
_SHUTDOWN_BINDING = re.compile(r"^sd1_[0-9a-f]{64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_CDHASH_HEX = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_GATEWAY_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_GATEWAY_IPV6 = re.compile(r"^[0-9A-Fa-f:.]+$")
_GATEWAY_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@/-]*$")
_SECRET_KEYS = frozenset(
    {
        "stream_key",
        "secret",
        "credential",
        "password",
        "api_key",
        "access_token",
        "authorization",
    }
)

# This label and framing are part of the v2 cross-language wire contract.
# Length-prefixing prevents concatenation ambiguity while keeping the raw
# session identity and launch nonce out of every event object.
_SESSION_BINDING_LABEL = b"tars-live-harness/session-binding/v2"
_ATTEMPT_BINDING_LABEL = b"tars-live-harness/attempt-binding/v2"
_LAUNCH_BINDING_LABEL = b"tars-live-harness/launch-nonce-binding/v2"
_SOURCE_BINDING_LABEL = b"tars-live-harness/source-binding/v2"
_OBSERVER_BINDING_LABEL = b"tars-live-harness/observer-binding/v2"
_PEER_FINGERPRINT_LABEL = b"tars-live-harness/peer-fingerprint/v1"
_SHUTDOWN_BINDING_LABEL = b"tars-live-harness/shutdown-binding/v1"


def session_binding(session_id: str, launch_nonce: str) -> str:
    """Return the one-way, domain-separated event reference for one session."""

    if (
        type(session_id) is not str
        or type(launch_nonce) is not str
        or _IDENTIFIER.fullmatch(session_id) is None
        or _PRODUCER_LAUNCH_NONCE.fullmatch(launch_nonce) is None
    ):
        raise HarnessProtocolError("session binding inputs are not producer identifiers")
    session_bytes = session_id.encode("utf-8")
    nonce_bytes = launch_nonce.encode("utf-8")
    if len(session_bytes) > 0xFFFFFFFF or len(nonce_bytes) > 0xFFFFFFFF:
        raise HarnessProtocolError("session binding input is too long")
    payload = (
        _SESSION_BINDING_LABEL
        + len(session_bytes).to_bytes(4, "big")
        + session_bytes
        + len(nonce_bytes).to_bytes(4, "big")
        + nonce_bytes
    )
    return "sb1_" + hashlib.sha256(payload).hexdigest()


def shutdown_nonce() -> str:
    """Create the fresh non-secret nonce that fences one stop request."""

    return "sn1_" + secrets.token_hex(16)


def shutdown_binding(session_ref: str, nonce: str) -> str:
    """Bind one shutdown nonce to the already-derived session reference."""

    if _SESSION_BINDING.fullmatch(session_ref) is None or _SHUTDOWN_NONCE.fullmatch(nonce) is None:
        raise HarnessProtocolError("shutdown binding inputs are invalid")
    session_bytes = session_ref.encode("ascii")
    nonce_bytes = nonce.encode("ascii")
    payload = (
        _SHUTDOWN_BINDING_LABEL
        + len(session_bytes).to_bytes(4, "big")
        + session_bytes
        + len(nonce_bytes).to_bytes(4, "big")
        + nonce_bytes
    )
    return "sd1_" + hashlib.sha256(payload).hexdigest()


def _binding128(label: bytes, value: str) -> str:
    if not isinstance(value, str):
        raise HarnessProtocolError("binding input must be a string")
    encoded = value.encode("utf-8")
    if len(encoded) > 0xFFFFFFFF:
        raise HarnessProtocolError("binding input is too long")
    return hashlib.sha256(label + len(encoded).to_bytes(4, "big") + encoded).hexdigest()[:32]


def attempt_binding(attempt_id: str) -> str:
    if type(attempt_id) is not str or _UUID.fullmatch(attempt_id) is None:
        raise HarnessProtocolError("attempt ID is not a producer UUID")
    # UUID text is producer-owned input; never expose it directly.  The
    # domain-separated digest also prevents a credential beginning with
    # ``at1_`` from colliding with a raw UUID spelling.
    return "at1_" + _binding128(_ATTEMPT_BINDING_LABEL, attempt_id.lower())


def validate_stream_key(value: object) -> str:
    """Validate the one shared producer stream-key grammar."""

    if type(value) is not str or _STREAM_KEY.fullmatch(value) is None:
        raise HarnessProtocolError("stream key is not the exact URL-safe producer credential")
    return value


def launch_binding(launch_nonce: str) -> str:
    if type(launch_nonce) is not str or _PRODUCER_LAUNCH_NONCE.fullmatch(launch_nonce) is None:
        raise HarnessProtocolError("launch nonce is not a producer identifier")
    return "ln1_" + _binding128(_LAUNCH_BINDING_LABEL, launch_nonce)


def source_binding(source_object: str) -> str:
    if type(source_object) is not str or re.fullmatch(r"ObjectIdentifier\(0x[0-9A-Fa-f]{1,32}\)", source_object) is None:
        raise HarnessProtocolError("source object is not an ObjectIdentifier")
    return "so1_" + _binding128(_SOURCE_BINDING_LABEL, source_object)


def observer_binding(observer_token: str) -> str:
    if type(observer_token) is not str or _UUID.fullmatch(observer_token) is None:
        raise HarnessProtocolError("observer token is not a producer UUID")
    return "ob1_" + _binding128(_OBSERVER_BINDING_LABEL, observer_token.lower())


def _require_role_binding(value: Any, pattern: re.Pattern[str], field: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise HarnessProtocolError(f"invalid event field: {field}")
    return value


def _reject_complete_secret(value: Any, stream_key: str, field: str) -> None:
    """Reject only the complete active credential at a key-bearing boundary."""

    if isinstance(value, str) and stream_key and stream_key in value:
        raise HarnessProtocolError(f"stream key material entered {field}")


def credential_material(value: str, sentinel: str | None) -> bool:
    """Return whether a complete string contains terminal credential material.

    A stream key is sensitive both when the complete sentinel occurs anywhere
    and when a non-empty proper prefix is the final bytes of a complete value.
    The latter rule closes boundaries that do not have a later pipe ``finish``
    call (rows, facts, JSON, and Markdown).  It intentionally leaves ordinary
    strings alone unless they end in a prefix of this active sentinel.
    """

    if not isinstance(value, str) or not isinstance(sentinel, str) or not sentinel:
        return False
    if sentinel in value:
        return True
    maximum = min(len(sentinel) - 1, len(value))
    return any(value.endswith(sentinel[:length]) for length in range(1, maximum + 1))


def redact_credential_material(value: str, sentinel: str | None) -> str:
    """Replace full or terminal-prefix credential material in one value."""

    if not isinstance(value, str) or not isinstance(sentinel, str) or not sentinel:
        return value
    redacted = value.replace(sentinel, "<redacted>")
    if value.endswith(sentinel):
        return redacted
    maximum = min(len(sentinel) - 1, len(value))
    for length in range(maximum, 0, -1):
        if value.endswith(sentinel[:length]):
            # Work from the original boundary so a replacement token cannot
            # accidentally affect prefix matching.  The head may itself have
            # contained one or more complete sentinels.
            redacted = value[:-length].replace(sentinel, "<redacted>") + "<redacted>"
            break
    return redacted

# This is the sole retained-evidence schema.  Callers may not smuggle an
# arbitrary diagnostic field into the JSON/Markdown projection: adding a new
# field requires extending this explicit allowlist and its tests.
EVIDENCE_FACT_ALLOWLIST = frozenset(
    {
        "app_path",
        "arch",
        "argv",
        "commit",
        "engine",
        "error",
        "events",
        "generated_by",
        "logs",
        "machine",
        "mic_bytes",
        "mic_frames",
        "mic_speech_frames",
        "phase_detail",
        "phase_rows",
        "retained",
        "segments_final",
        "segments_pre_stop",
        "segments_total",
        "signed_app",
        "tcc_message",
        "timestamp",
        "transcript",
        "transcript_speakers",
        "transcript_candidate_words",
        "transcript_interviewer_words",
        "transcript_candidate_hits",
        "transcript_interviewer_hits",
        "transcript_valid_typed",
        "transcript_restart_match",
        "transcription_complete",
        "tree_state",
        "voice",
    }
)

# Kept here rather than importing the live verifier so the canonical evidence
# boundary remains independently callable and cannot inherit a loose enum or
# row implementation through a module cycle.
PHASE_ID_VALUES = frozenset(
    {
        "Preflight proveniência da árvore",
        "Preflight ADC",
        "Preflight porta",
        "Preflight voz pt-BR",
        "Preflight app assinado",
        "Preflight proveniência/assinatura do app",
        "Backend up",
        "Sessão criada",
        "Chave inválida rejeitada",
        "Chave válida aceita (controle positivo)",
        "Companion — estado da captura Process Tap",
        "Áudio do candidato reproduzido",
        "Canal do entrevistador enviado",
        "Reinício do companion",
        "Canal do entrevistador sustentado até o /stop",
        "Companion — fatos positivos antes da parada",
        "Companion — cleanup após rejeição",
        "Companion — cleanup após falha terminal",
        "Companion — cleanup após falha",
        "Companion — cleanup após a parada",
        "Sessão encerrada",
        "Segmento final rotulado 'Candidato'",
        "Segmento final rotulado 'Entrevistador'",
        "Sem duplicação entre falantes",
        "Fala pós-reinício transcrita",
        "Documento de evidência secret-safe",
    }
)
PHASE_STATUS_VALUES = frozenset({"PASS", "FAIL", "BLOQUEADO", "INCONCLUSIVE", "PULADO"})


class _TypedPhaseRow(dict[str, object]):
    """Exact producer-owned row marker shared by verifier and canonicalizer."""

    producer_owned = True

# Fixed schema names and enum values are producer-owned.  They are not
# untrusted dynamic material merely because a credential sentinel happens to
# end with the same character.  A complete sentinel collision still remains
# a failure and is replaced by the typed redactors at the boundary.
_OPERATIONAL_FACT_KEYS = frozenset(
    {
        "expected_head",
        "expected_tree",
        "expected_digest",
        "artifact_facts",
        "process_tap_positive",
        "process_tap_evidence_result",
        "proof_digest",
        "restart_drill",
    }
)
_FIXED_FACT_KEYS = EVIDENCE_FACT_ALLOWLIST | _OPERATIONAL_FACT_KEYS
_PRODUCER_FACT_KEYS = frozenset(
    {
        "app_path", "arch", "commit", "engine", "generated_by", "machine",
        "mic_bytes", "mic_frames", "mic_speech_frames", "phase_rows",
        "segments_final", "segments_pre_stop", "segments_total", "signed_app",
        "timestamp", "transcript_speakers", "transcript_candidate_words",
        "transcript_interviewer_words", "transcript_candidate_hits",
        "transcript_interviewer_hits", "transcript_valid_typed",
        "transcript_restart_match", "transcription_complete", "tree_state", "voice",
        "proof_digest",
        *_OPERATIONAL_FACT_KEYS,
    }
)
_CONTROLLED_VALUES_BY_KEY = {
    "status": frozenset({"PASS", "FAIL", "BLOQUEADO", "INCONCLUSIVE", "PULADO"}),
    "result": frozenset({"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"}),
    "process_tap_evidence_result": frozenset({"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"}),
    "process_tap_positive": frozenset({True, False}),
    "restart_drill": frozenset({True, False}),
    "engine": frozenset({PROCESS_TAP, SCREEN_CAPTURE_KIT}),
    "speaker": frozenset({"Candidato", "Entrevistador"}),
    "transcript_speakers": frozenset({"Candidato", "Entrevistador"}),
    "transcript_candidate_words": frozenset({"candidato", "experiencia", "vendas", "ingles"}),
    "transcript_interviewer_words": frozenset({"entrevistador", "pergunta"}),
}
def redact_fixed_material(value: str, sentinel: str | None) -> str:
    """Redact complete sentinels in producer-owned schema strings only."""

    if not isinstance(value, str) or not isinstance(sentinel, str) or not sentinel:
        return value
    return value.replace(sentinel, "<redacted>")


def _schema_mapping_kind(value: Mapping[Any, Any]) -> str | None:
    keys = set(value)
    if keys == {"name", "status", "detail"}:
        return "phase_row_producer" if getattr(value, "producer_owned", False) else "phase_row"
    if keys == {"speaker", "text"}:
        return "transcript_row"
    if keys in ({"result", "facts"}, {"result", "facts", "claim"}):
        return "evidence_document"
    if keys == {
        "provenance_head", "provenance_tree", "dirty", "bundle_id", "team_id",
        "hardened_runtime", "entitlements", "strict_signature", "executable_digest",
        "sealed_executable_digest", "provenance_digest", "developer_id_authority",
        "audio_input_entitlement", "static_identity",
    }:
        return "artifact_facts"
    if keys == {"unique_cdhash", "designated_requirement"}:
        return "static_identity"
    return None


def _typed_key_is_fixed(key: object, kind: str | None, *, top_level: bool) -> bool:
    key_text = key if isinstance(key, str) else str(key)
    if kind in {"phase_row", "phase_row_producer"}:
        return key_text in {"name", "status", "detail"}
    if kind == "transcript_row":
        return key_text in {"speaker", "text"}
    if kind == "evidence_document":
        return top_level and key_text in {"result", "facts", "claim"}
    if kind == "artifact_facts":
        return key_text in {
            "provenance_head", "provenance_tree", "dirty", "bundle_id", "team_id",
            "hardened_runtime", "entitlements", "strict_signature", "executable_digest",
            "sealed_executable_digest", "provenance_digest", "developer_id_authority",
            "audio_input_entitlement", "static_identity",
        }
    if kind == "static_identity":
        return key_text in {"unique_cdhash", "designated_requirement"}
    return top_level and key_text in _FIXED_FACT_KEYS


def _typed_enum_is_fixed(key: str, value: object) -> bool:
    allowed = _CONTROLLED_VALUES_BY_KEY.get(key)
    if allowed is None or not isinstance(value, (str, bool, int, float, type(None))):
        return False
    return value in allowed


def _typed_closed_list_is_fixed(key: str, value: object) -> bool:
    """Recognize only the two producer-owned closed word/speaker lists."""

    allowed = _CONTROLLED_VALUES_BY_KEY.get(key)
    return (
        allowed is not None
        and key in {"transcript_speakers", "transcript_candidate_words", "transcript_interviewer_words"}
        and isinstance(value, (list, tuple, set))
        and all(isinstance(item, str) and item in allowed for item in value)
    )


def _fixed_schema_string(value: object, sentinel: str | None) -> bool:
    """Whether a fixed-schema string contains a full sentinel."""

    return isinstance(value, str) and isinstance(sentinel, str) and bool(sentinel) and sentinel in value


def _typed_secret_free(value: Any, sentinel: str | None, *, top_level: bool = False) -> bool:
    if isinstance(value, str):
        return not credential_material(value, sentinel)
    if isinstance(value, Mapping):
        kind = _schema_mapping_kind(value)
        for key, item in value.items():
            key_text = key if isinstance(key, str) else str(key)
            key_fixed = _typed_key_is_fixed(key, kind, top_level=top_level)
            if key_fixed:
                if _fixed_schema_string(key_text, sentinel):
                    return False
            elif not _typed_secret_free(key_text, sentinel):
                return False
            if kind == "phase_row_producer" and isinstance(item, str):
                # Typed production rows carry closed labels/details.  The
                # ownership marker is an in-memory tag and is lost on JSON
                # serialization; all arbitrary rows remain dynamic.
                if _fixed_schema_string(item, sentinel):
                    return False
                continue
            if kind == "artifact_facts" and isinstance(item, str):
                if _fixed_schema_string(item, sentinel):
                    return False
                continue
            if kind == "artifact_facts" and key_text == "entitlements":
                if isinstance(item, list) and any(_fixed_schema_string(entry, sentinel) for entry in item):
                    return False
                continue
            if kind == "static_identity" and isinstance(item, str):
                if _fixed_schema_string(item, sentinel):
                    return False
                continue
            if top_level and key_text in _PRODUCER_FACT_KEYS:
                # Producer-owned scalar/closed values use complete-sentinel
                # checks only.  A URL-safe first character is not evidence of
                # a credential and must not collide with a value such as
                # ``PASS`` or a machine/voice label.
                if isinstance(item, str) and _fixed_schema_string(item, sentinel):
                    return False
                if key_text == "phase_rows":
                    if not _typed_secret_free(item, sentinel):
                        return False
                elif _typed_closed_list_is_fixed(key_text, item):
                    if any(_fixed_schema_string(item_value, sentinel) for item_value in item):
                        return False
                continue
            if _typed_enum_is_fixed(key_text, item):
                if isinstance(item, str) and _fixed_schema_string(item, sentinel):
                    return False
                continue
            if _typed_closed_list_is_fixed(key_text, item):
                if any(_fixed_schema_string(item_value, sentinel) for item_value in item):
                    return False
                continue
            child_top_level = kind == "evidence_document" and key_text == "facts"
            if not _typed_secret_free(item, sentinel, top_level=child_top_level):
                return False
        return True
    if isinstance(value, (list, tuple, set)):
        return all(_typed_secret_free(item, sentinel) for item in value)
    return True


def _typed_contains_secret_material(value: Any, sentinel: str | None, *, top_level: bool = False) -> bool:
    if isinstance(value, str):
        return credential_material(value, sentinel)
    if isinstance(value, Mapping):
        kind = _schema_mapping_kind(value)
        for key, item in value.items():
            key_text = key if isinstance(key, str) else str(key)
            key_fixed = _typed_key_is_fixed(key, kind, top_level=top_level)
            if key_fixed:
                if _fixed_schema_string(key_text, sentinel):
                    return True
            elif _typed_contains_secret_material(key_text, sentinel):
                return True
            if isinstance(key, str) and key.lower() in _SECRET_KEYS:
                return True
            if kind == "phase_row_producer" and isinstance(item, str):
                if _fixed_schema_string(item, sentinel):
                    return True
                continue
            if kind == "artifact_facts" and isinstance(item, str):
                if _fixed_schema_string(item, sentinel):
                    return True
                continue
            if kind == "artifact_facts" and key_text == "entitlements":
                if isinstance(item, list) and any(_fixed_schema_string(entry, sentinel) for entry in item):
                    return True
                continue
            if kind == "static_identity" and isinstance(item, str):
                if _fixed_schema_string(item, sentinel):
                    return True
                continue
            if top_level and key_text in _PRODUCER_FACT_KEYS:
                if isinstance(item, str) and _fixed_schema_string(item, sentinel):
                    return True
                if key_text == "phase_rows" and _typed_contains_secret_material(item, sentinel):
                    return True
                if _typed_closed_list_is_fixed(key_text, item) and any(
                    _fixed_schema_string(item_value, sentinel) for item_value in item
                ):
                    return True
                continue
            if _typed_enum_is_fixed(key_text, item):
                if isinstance(item, str) and _fixed_schema_string(item, sentinel):
                    return True
                continue
            if _typed_closed_list_is_fixed(key_text, item):
                if any(_fixed_schema_string(item_value, sentinel) for item_value in item):
                    return True
                continue
            child_top_level = kind == "evidence_document" and key_text == "facts"
            if _typed_contains_secret_material(item, sentinel, top_level=child_top_level):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_typed_contains_secret_material(item, sentinel) for item in value)
    return False


class HarnessProtocolError(ValueError):
    """A fail-closed protocol, policy, or liveness rejection."""


def _der_length(size: int) -> bytes:
    if size < 0:
        raise HarnessProtocolError("DER length is negative")
    if size < 0x80:
        return bytes((size,))
    raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
    if len(raw) > 126:
        raise HarnessProtocolError("DER length is outside bounds")
    return bytes((0x80 | len(raw),)) + raw


def _der_tlv(tag: int, content: bytes) -> bytes:
    if type(content) is not bytes:
        raise HarnessProtocolError("DER content must be exact bytes")
    return bytes((tag,)) + _der_length(len(content)) + content


def _der_integer(value: int) -> bytes:
    if type(value) is not int or value < 0 or value > (1 << 63) - 1:
        raise HarnessProtocolError("LWCR integer is outside bounds")
    raw = value.to_bytes((value.bit_length() + 7) // 8 or 1, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return _der_tlv(0x02, raw)


def _der_utf8(value: str) -> bytes:
    if type(value) is not str or "\x00" in value:
        raise HarnessProtocolError("LWCR string is malformed")
    encoded = value.encode("utf-8")
    if not 1 <= len(encoded) <= 1024:
        raise HarnessProtocolError("LWCR string length is outside bounds")
    return _der_tlv(0x0C, encoded)


def _der_dictionary(items: Mapping[str, object]) -> bytes:
    if type(items) is not dict or not items:
        raise HarnessProtocolError("LWCR dictionary is empty or not exact")
    pairs = bytearray()
    for key in sorted(items):
        if type(key) is not str:
            raise HarnessProtocolError("LWCR dictionary key is not a string")
        pairs.extend(_der_tlv(0x30, _der_utf8(key) + _der_value(items[key])))
    return _der_tlv(0xB0, bytes(pairs))


def _der_value(value: object) -> bytes:
    if type(value) is bool:
        raise HarnessProtocolError("LWCR boolean facts are unsupported")
    if type(value) is int:
        return _der_integer(value)
    if type(value) is str:
        return _der_utf8(value)
    if type(value) is dict:
        return _der_dictionary(value)
    raise HarnessProtocolError("LWCR fact type is unsupported")


def encode_lightweight_code_requirement(facts: Mapping[str, object]) -> bytes:
    """Encode a default-designated LWCR facts dictionary into kernel DER.

    Apple returns ``kSecCodeInfoDefaultDesignatedLightweightCodeRequirement``
    as a decoded CFDictionary, not a SecRequirement. Kernel guest matching
    requires an lwcrForm requirement whose payload is the CoreEntitlements V1
    envelope around ``{ccat,comp,reqs,vers}``.
    """

    if type(facts) is not dict:
        raise HarnessProtocolError("LWCR facts must be an exact dictionary")
    wrapped = {
        "ccat": 0,
        "comp": 1,
        "reqs": facts,
        "vers": 1,
    }
    return _der_tlv(0x70, _der_integer(1) + _der_dictionary(wrapped))


@dataclass(frozen=True)
class StaticCodeIdentity:
    """The immutable identity and designated requirement of one static app.

    Security.framework owns the representation of both values.  The harness
    retains only exact copied bytes, never a printable CDHash or a borrowed
    CoreFoundation object.  Length checks are deliberately independent of the
    algorithm so future CDHash sizes remain safe without being guessed.
    """

    unique_cdhash: bytes
    designated_requirement: bytes
    lightweight_requirement: bytes

    def __post_init__(self) -> None:
        if (
            type(self.unique_cdhash) is not bytes
            or not 1 <= len(self.unique_cdhash) <= 64
        ):
            raise ValueError("static unique code hash must contain 1..64 raw bytes")
        if (
            type(self.designated_requirement) is not bytes
            or not 1 <= len(self.designated_requirement) <= 65_536
        ):
            raise ValueError("designated requirement must contain 1..65536 raw bytes")
        if (
            type(self.lightweight_requirement) is not bytes
            or not 1 <= len(self.lightweight_requirement) <= 65_536
        ):
            raise ValueError("lightweight requirement must contain 1..65536 raw bytes")


def require_exact_static_code_identity(
    value: object,
    expected: object | None = None,
    *,
    label: str = "static code identity",
) -> StaticCodeIdentity:
    """Authorize only an exact identity object and (optionally) raw-byte match.

    A frozen dataclass is not a security boundary: a subclass can override
    ``__eq__``/``__ne__`` and a hostile fixture can replace fields through
    reflection.  Keep the check at every crossing and compare the copied
    bytes directly with constant-time comparison.  No dataclass equality is
    consulted here.
    """

    if type(value) is not StaticCodeIdentity:
        raise HarnessProtocolError(f"{label} must be an exact StaticCodeIdentity")
    identity = value
    if type(identity.unique_cdhash) is not bytes or not 1 <= len(identity.unique_cdhash) <= 64:
        raise HarnessProtocolError(f"{label} unique code hash is malformed")
    if (
        type(identity.designated_requirement) is not bytes
        or not 1 <= len(identity.designated_requirement) <= 65_536
    ):
        raise HarnessProtocolError(f"{label} designated requirement is malformed")
    if (
        type(identity.lightweight_requirement) is not bytes
        or not 1 <= len(identity.lightweight_requirement) <= 65_536
    ):
        raise HarnessProtocolError(f"{label} lightweight requirement is malformed")
    if expected is not None:
        if type(expected) is not StaticCodeIdentity:
            raise HarnessProtocolError("expected static code identity is not exact")
        if (
            type(expected.unique_cdhash) is not bytes
            or not 1 <= len(expected.unique_cdhash) <= 64
            or type(expected.designated_requirement) is not bytes
            or not 1 <= len(expected.designated_requirement) <= 65_536
            or type(expected.lightweight_requirement) is not bytes
            or not 1 <= len(expected.lightweight_requirement) <= 65_536
        ):
            raise HarnessProtocolError("expected static code identity is malformed")
        if not hmac.compare_digest(identity.unique_cdhash, expected.unique_cdhash):
            raise HarnessProtocolError(f"{label} unique code hash mismatch")
        if not hmac.compare_digest(
            identity.designated_requirement,
            expected.designated_requirement,
        ):
            raise HarnessProtocolError(f"{label} designated requirement mismatch")
        if not hmac.compare_digest(
            identity.lightweight_requirement,
            expected.lightweight_requirement,
        ):
            raise HarnessProtocolError(f"{label} lightweight requirement mismatch")
    return identity


class StaticCodeIdentityReader(Protocol):
    """Injectable static Security.framework identity boundary."""

    def __call__(self, app_path: Path) -> StaticCodeIdentity:
        ...


def canonical_json(value: Any) -> bytes:
    """Return the one JSON byte encoding shared with Foundation."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise HarnessProtocolError("value cannot be canonicalized") from exc


def shared_golden_session_command_payload() -> bytes:
    """Read the one canonical command fixture from the Swift source.

    Keeping the literal in ``LiveHarnessProtocol.swift`` gives the Python and
    Swift tests one source of truth without adding a 16th repository path.
    """

    swift_path = (
        Path(__file__).resolve().parent.parent
        / "companion/native-macos/Sources/TarsNativeCompanion/LiveHarnessProtocol.swift"
    )
    try:
        source = swift_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HarnessProtocolError("shared Swift golden fixture is unavailable") from exc
    match = re.search(
        r"public static let sessionCommandPayload = Data\(#\"(?P<payload>\{.*?\})\"#\.utf8\)",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise HarnessProtocolError("shared Swift golden fixture marker is missing")
    payload = match.group("payload").encode("utf-8")
    decode_session_command(payload)
    return payload


def frame(payload: bytes) -> bytes:
    if not payload or len(payload) > MAX_PAYLOAD:
        raise HarnessProtocolError("payload length outside bounds")
    return len(payload).to_bytes(4, "big") + payload


class FrameDecoder:
    """Incremental four-byte big-endian framing for stream fragmentation."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self._buffer.extend(data)
        result: list[bytes] = []
        while len(self._buffer) >= 4:
            size = int.from_bytes(self._buffer[:4], "big")
            if size == 0:
                raise HarnessProtocolError("zero-length payload")
            if size > MAX_PAYLOAD:
                raise HarnessProtocolError("payload too large")
            if len(self._buffer) < size + 4:
                break
            result.append(bytes(self._buffer[4 : size + 4]))
            del self._buffer[: size + 4]
        return result

    def finish(self) -> None:
        if self._buffer:
            raise HarnessProtocolError("truncated frame")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HarnessProtocolError(f"duplicate field: {key}")
        result[key] = value
    return result


def _decode_canonical_object(payload: bytes) -> dict[str, Any]:
    if not payload or len(payload) > MAX_PAYLOAD:
        raise HarnessProtocolError("payload length outside bounds")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                HarnessProtocolError(f"invalid constant: {constant}")
            ),
        )
    except HarnessProtocolError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessProtocolError("malformed JSON") from exc
    if not isinstance(value, dict):
        raise HarnessProtocolError("message must be an object")
    if canonical_json(value) != payload:
        raise HarnessProtocolError("message is not canonical or has trailing bytes")
    return value


def _require_identifier(value: Any, field: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise HarnessProtocolError(f"invalid field: {field}")
    return value


def validate_gateway_base(value: str) -> str:
    """Validate the keyless, canonical websocket gateway base.

    The grammar is deliberately implemented without a URL normalizer so the
    Python and Foundation implementations can share the same admission
    boundary.  Percent encoding is rejected entirely: accepting an encoded
    spelling would make the authority/path depend on a later decoder.
    """

    if not isinstance(value, str) or not value:
        raise HarnessProtocolError("gateway base is empty")
    if len(value.encode("utf-8")) > 2048:
        raise HarnessProtocolError("gateway base is too long")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
        raise HarnessProtocolError("gateway base contains whitespace or control")
    if any(character in value for character in ("\\", "%", "?", "#")):
        raise HarnessProtocolError("gateway base contains ambiguous delimiters")
    match = re.fullmatch(r"(?P<scheme>ws|wss)://(?P<authority>[^/]+)(?P<path>/.*)?", value)
    if match is None:
        raise HarnessProtocolError("gateway base is not an absolute ws/wss URL")
    authority = match.group("authority")
    path = match.group("path")
    if "@" in authority or not authority:
        raise HarnessProtocolError("gateway base contains userinfo")

    host: str
    port: str | None
    if authority.startswith("["):
        close = authority.find("]")
        if close <= 1 or not _GATEWAY_IPV6.fullmatch(authority[1:close]):
            raise HarnessProtocolError("gateway base IPv6 host is invalid")
        host = authority[1:close]
        suffix = authority[close + 1 :]
        if suffix and not suffix.startswith(":"):
            raise HarnessProtocolError("gateway base authority is invalid")
        port = suffix[1:] if suffix else None
        if ":" not in host:
            raise HarnessProtocolError("gateway base IPv6 host is invalid")
        if "." in host:
            raise HarnessProtocolError("gateway IPv4-mapped IPv6 host is invalid")
    else:
        if any(character in authority for character in "[]") or authority.count(":") > 1:
            raise HarnessProtocolError("gateway base authority is invalid")
        host, separator, port = authority.partition(":")
        if not separator:
            port = None
        if not host or host != host.lower():
            raise HarnessProtocolError("gateway base host is not canonical")
        labels = host.split(".")
        if len(host) > 253 or any(
            not label or len(label) > 63 or _GATEWAY_HOST.fullmatch(label) is None
            for label in labels
        ):
            raise HarnessProtocolError("gateway base host is invalid")
    if port is not None:
        if not port.isdigit() or not 1 <= int(port) <= 65_535:
            raise HarnessProtocolError("gateway base port is invalid")
    if path is not None:
        if len(path.encode("ascii")) > 1024 or _GATEWAY_PATH.fullmatch(path) is None:
            raise HarnessProtocolError("gateway base path is invalid")
        if "//" in path or path == "/" or path.endswith("/"):
            raise HarnessProtocolError("gateway base path is ambiguous")
    return value


def validate_gateway_base_for_session(value: str, stream_key: str) -> str:
    """Validate a gateway base and prove its separately supplied key is absent."""

    validate_gateway_base(value)
    validate_stream_key(stream_key)
    if stream_key in value:
        raise HarnessProtocolError("stream key material entered gateway base")
    # ``validate_gateway_base`` rejects every percent sign, so there is no
    # encoded spelling to decode.  Keep this explicit assertion beside the
    # raw check so future grammar changes cannot silently reopen that edge.
    if "%" in value:
        raise HarnessProtocolError("encoded stream key material entered gateway base")
    return value


def decode_session_command(payload: bytes) -> dict[str, Any]:
    value = _decode_canonical_object(payload)
    if set(value) != COMMAND_FIELDS:
        raise HarnessProtocolError("exact command field allowlist violation")
    version = value["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != PROTOCOL_VERSION:
        raise HarnessProtocolError("unsupported protocol version")
    if value["type"] != "session":
        raise HarnessProtocolError("unsupported message type")
    session_id = _require_identifier(value["session_id"], "session_id")
    launch_nonce = _require_identifier(value["launch_nonce"], "launch_nonce")
    stream_key = value["stream_key"]
    validate_stream_key(stream_key)
    # The command is the one place where the complete credential is present.
    # Cross-check the complete sentinel only; a first-character coincidence
    # must never reject valid URL-safe producer values.
    _reject_complete_secret(session_id, stream_key, "session_id")
    _reject_complete_secret(launch_nonce, stream_key, "launch_nonce")
    validate_gateway_base_for_session(value["gateway"], stream_key)
    return value


def encode_session_command(*, session_id: str, stream_key: str, gateway: str, launch_nonce: str) -> bytes:
    validate_stream_key(stream_key)
    _require_identifier(session_id, "session_id")
    _require_identifier(launch_nonce, "launch_nonce")
    _reject_complete_secret(session_id, stream_key, "session_id")
    _reject_complete_secret(launch_nonce, stream_key, "launch_nonce")
    validate_gateway_base_for_session(gateway, stream_key)
    payload = {
        "gateway": gateway,
        "launch_nonce": launch_nonce,
        "session_id": session_id,
        "stream_key": stream_key,
        "type": "session",
        "version": PROTOCOL_VERSION,
    }
    decode_session_command(canonical_json(payload))
    return frame(canonical_json(payload))


def decode_shutdown_request(payload: bytes) -> dict[str, Any]:
    value = _decode_canonical_object(payload)
    if set(value) != SHUTDOWN_REQUEST_FIELDS:
        raise HarnessProtocolError("exact shutdown request field allowlist violation")
    if type(value.get("version")) is not int or value["version"] != PROTOCOL_VERSION:
        raise HarnessProtocolError("unsupported shutdown request version")
    if value.get("type") != "shutdown":
        raise HarnessProtocolError("unsupported shutdown request type")
    if type(value.get("shutdown_nonce")) is not str or _SHUTDOWN_NONCE.fullmatch(value["shutdown_nonce"]) is None:
        raise HarnessProtocolError("invalid shutdown nonce")
    if type(value.get("shutdown_binding")) is not str or _SHUTDOWN_BINDING.fullmatch(value["shutdown_binding"]) is None:
        raise HarnessProtocolError("invalid shutdown binding")
    return value


def encode_shutdown_request(*, session_ref: str, nonce: str) -> bytes:
    binding = shutdown_binding(session_ref, nonce)
    payload = {
        "shutdown_binding": binding,
        "shutdown_nonce": nonce,
        "type": "shutdown",
        "version": PROTOCOL_VERSION,
    }
    decode_shutdown_request(canonical_json(payload))
    return frame(canonical_json(payload))


def decode_shutdown_ack(
    payload: bytes,
    *,
    expected_session_ref: str,
    expected_nonce: str,
) -> dict[str, Any]:
    value = _decode_canonical_object(payload)
    if set(value) != SHUTDOWN_ACK_FIELDS:
        raise HarnessProtocolError("exact shutdown acknowledgement field allowlist violation")
    if type(value.get("version")) is not int or value["version"] != PROTOCOL_VERSION:
        raise HarnessProtocolError("unsupported shutdown acknowledgement version")
    if value.get("type") != "shutdown_ack" or value.get("status") != "stopped":
        raise HarnessProtocolError("invalid shutdown acknowledgement")
    if type(value.get("shutdown_nonce")) is not str or _SHUTDOWN_NONCE.fullmatch(value["shutdown_nonce"]) is None:
        raise HarnessProtocolError("invalid shutdown acknowledgement nonce")
    if type(value.get("shutdown_binding")) is not str or _SHUTDOWN_BINDING.fullmatch(value["shutdown_binding"]) is None:
        raise HarnessProtocolError("invalid shutdown acknowledgement binding")
    if value["shutdown_nonce"] != expected_nonce or value["shutdown_binding"] != shutdown_binding(expected_session_ref, expected_nonce):
        raise HarnessProtocolError("shutdown acknowledgement binding mismatch")
    return value


def encode_shutdown_ack(*, session_ref: str, nonce: str) -> bytes:
    binding = shutdown_binding(session_ref, nonce)
    payload = {
        "shutdown_binding": binding,
        "shutdown_nonce": nonce,
        "status": "stopped",
        "type": "shutdown_ack",
        "version": PROTOCOL_VERSION,
    }
    decode_shutdown_ack(payload=canonical_json(payload), expected_session_ref=session_ref, expected_nonce=nonce)
    return frame(canonical_json(payload))


def _status_object(status: Any, *, actual_engine: str) -> dict[str, Any]:
    if type(status) is not dict:
        raise HarnessProtocolError("health status must be an object")
    result = dict(status)
    status_kind = result.get("kind")
    expected_fields = HEALTH_FAILURE_FIELDS if status_kind == "failed" else HEALTH_FIELDS
    if set(result) != expected_fields:
        raise HarnessProtocolError("health status field allowlist violation")
    if type(status_kind) is not str or status_kind not in HEALTH_KIND_VALUES:
        raise HarnessProtocolError("invalid health status kind")
    enum_values = {
        "permission": HEALTH_PERMISSION_VALUES,
        "route": HEALTH_ROUTE_VALUES,
        "interruption": HEALTH_INTERRUPTION_VALUES,
        "sleep": HEALTH_SLEEP_VALUES,
    }
    for key, allowed in enum_values.items():
        if type(result.get(key)) is not str or result[key] not in allowed:
            raise HarnessProtocolError(f"invalid health status field: {key}")
    if type(result.get("overflowed")) is not bool:
        raise HarnessProtocolError("invalid health overflow field")
    if status_kind == "failed":
        if result["route"] != "unknown" or result["interruption"] != "clear" \
                or result["sleep"] != "awake" or result["overflowed"] is not False:
            raise HarnessProtocolError("failed health status constants are invalid")
        failure_code = result.get("failure_code")
        try:
            failure_code_enum = LiveHarnessFailureCode(failure_code)
        except (TypeError, ValueError):
            raise HarnessProtocolError("failed health status lacks a closed failure code")
        permission = result.get("permission")
        if permission not in {"unknown", "denied"}:
            raise HarnessProtocolError("failed health permission is not non-granting")
        if (permission == "denied") != (
            failure_code_enum is LiveHarnessFailureCode.PERMISSION_DENIED
        ):
            raise HarnessProtocolError("failed health permission/code mismatch")
    elif actual_engine not in {PROCESS_TAP, SCREEN_CAPTURE_KIT}:
        raise HarnessProtocolError("health status has no closed actual engine")
    return result


def _reject_secret_in_event(value: Any, stream_key: str, field: str = "event") -> None:
    """Reject the complete active sentinel in every decoded event string."""

    if isinstance(value, str):
        _reject_complete_secret(value, stream_key, field)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _reject_secret_in_event(key, stream_key, f"{field}.key")
            _reject_secret_in_event(item, stream_key, f"{field}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secret_in_event(item, stream_key, f"{field}[{index}]")


def decode_event(payload: bytes, *, stream_key: str) -> dict[str, Any]:
    validate_stream_key(stream_key)
    if stream_key.encode("utf-8") in payload:
        raise HarnessProtocolError("stream key material entered event wire")
    value = _decode_canonical_object(payload)
    kind = value.get("kind")
    if type(kind) is not str or kind not in EVENT_KINDS:
        raise HarnessProtocolError("invalid event kind")
    expected = EVENT_BASE_FIELDS | ({"status"} if kind == "health" else set())
    if set(value) != expected:
        raise HarnessProtocolError("exact event field allowlist violation")
    version = value.get("version")
    if version != PROTOCOL_VERSION or isinstance(version, bool) or not isinstance(version, int):
        raise HarnessProtocolError("unsupported event version")
    if value.get("type") != "event":
        raise HarnessProtocolError("unsupported event message type")
    _require_role_binding(value.get("attempt_id"), _ATTEMPT_ID, "attempt_id")
    _require_role_binding(value.get("launch_nonce"), _LAUNCH_NONCE, "launch_nonce")
    _require_role_binding(value.get("session_binding"), _SESSION_BINDING, "session_binding")
    _require_role_binding(value.get("source_binding"), _SOURCE_BINDING, "source_binding")
    _require_role_binding(value.get("observer_binding"), _OBSERVER_BINDING, "observer_binding")
    for field in ("requested_engine", "resolved_engine", "actual_engine"):
        if type(value.get(field)) is not str or value.get(field) not in {PROCESS_TAP, SCREEN_CAPTURE_KIT}:
            raise HarnessProtocolError(f"invalid event engine: {field}")
    generation = value.get("generation")
    if type(generation) is not int or not 1 <= generation <= (1 << 64) - 1:
        raise HarnessProtocolError("invalid event generation")
    if kind == "health":
        value["status"] = _status_object(value["status"], actual_engine=value["actual_engine"])
    _reject_secret_in_event(value, stream_key)
    return value


def encode_event(event: Mapping[str, Any], *, stream_key: str) -> bytes:
    validate_stream_key(stream_key)
    payload = dict(event)
    encoded = canonical_json(payload)
    decode_event(encoded, stream_key=stream_key)
    if stream_key.encode("utf-8") in encoded:
        raise HarnessProtocolError("stream key material entered event wire")
    return frame(encoded)


@dataclass(frozen=True)
class PeerIdentity:
    euid: int
    pid: int | None
    audit_token: str | None
    executable_path: str | None


def _peer_field_types_are_exact(peer: PeerIdentity) -> bool:
    return (
        type(peer.euid) is int
        and 0 <= peer.euid <= (1 << 64) - 1
        and (peer.pid is None or (type(peer.pid) is int and 0 < peer.pid <= (1 << 64) - 1))
        and (peer.audit_token is None or type(peer.audit_token) is str)
        and (peer.executable_path is None or type(peer.executable_path) is str)
    )


def peer_identity_equal(actual: object, expected: object) -> bool:
    """Compare complete peer fields without invoking dataclass equality."""

    if type(actual) is not PeerIdentity or type(expected) is not PeerIdentity:
        return False
    actual_peer = actual
    expected_peer = expected
    if not _peer_field_types_are_exact(actual_peer) or not _peer_field_types_are_exact(expected_peer):
        return False
    if actual_peer.euid != expected_peer.euid or actual_peer.pid != expected_peer.pid:
        return False
    if actual_peer.audit_token is None or expected_peer.audit_token is None:
        if actual_peer.audit_token is not expected_peer.audit_token:
            return False
    elif not hmac.compare_digest(
        actual_peer.audit_token.encode("utf-8"), expected_peer.audit_token.encode("utf-8")
    ):
        return False
    if actual_peer.executable_path is None or expected_peer.executable_path is None:
        return actual_peer.executable_path is expected_peer.executable_path
    return hmac.compare_digest(
        actual_peer.executable_path.encode("utf-8"),
        expected_peer.executable_path.encode("utf-8"),
    )


def peer_matches(actual: PeerIdentity, expected: PeerIdentity) -> bool:
    """Apply the expected peer's optional fields as wildcard constraints."""

    if type(actual) is not PeerIdentity or type(expected) is not PeerIdentity:
        return False
    if not _peer_field_types_are_exact(actual) or not _peer_field_types_are_exact(expected):
        return False
    if actual.euid != expected.euid:
        return False
    if expected.pid is not None and actual.pid != expected.pid:
        return False
    if expected.audit_token is not None:
        if actual.audit_token is None or not hmac.compare_digest(
            actual.audit_token.encode("utf-8"), expected.audit_token.encode("utf-8")
        ):
            return False
    if expected.executable_path is not None:
        if actual.executable_path is None or not hmac.compare_digest(
            actual.executable_path.encode("utf-8"), expected.executable_path.encode("utf-8")
        ):
            return False
    return True


def peer_fingerprint(peer: PeerIdentity) -> str:
    """Return the stable, domain-separated binding for a complete peer.

    The previous printable ``euid:pid:audit:path`` value was both ambiguous
    and an accidental serialization of kernel identity.  A complete peer is
    now length-framed field-by-field before hashing, so delimiters, unicode,
    and field-boundary concatenations cannot collide.  This is a binding, not
    a secret or an authorization token; callers must still revalidate the
    complete peer at each lifecycle edge.
    """
    if (
        type(peer) is not PeerIdentity
        or not _peer_field_types_are_exact(peer)
        or peer.pid is None
        or peer.audit_token is None
        or peer.executable_path is None
        or not peer.audit_token
        or not peer.executable_path
        or not peer.executable_path.startswith("/")
    ):
        raise HarnessProtocolError("kernel peer identity is incomplete")
    _audit_token_bytes(peer.audit_token)
    try:
        audit = peer.audit_token.encode("utf-8")
        executable = peer.executable_path.encode("utf-8")
    except (UnicodeEncodeError, AttributeError) as exc:
        raise HarnessProtocolError("kernel peer identity is not encodable") from exc
    if (
        not audit
        or not executable
        or b"\0" in audit
        or b"\0" in executable
        or len(audit) > 0xFFFFFFFF
        or len(executable) > 0xFFFFFFFF
    ):
        raise HarnessProtocolError("kernel peer identity field is too long")
    payload = (
        _PEER_FINGERPRINT_LABEL
        + peer.euid.to_bytes(8, "big", signed=False)
        + peer.pid.to_bytes(8, "big", signed=False)
        + len(audit).to_bytes(4, "big")
        + audit
        + len(executable).to_bytes(4, "big")
        + executable
    )
    return "pb1_" + hashlib.sha256(payload).hexdigest()


class DarwinPeerIdentityReader:
    """Small macOS kernel-peer boundary used only on an accepted descriptor."""

    # These are the SDK values from <sys/socket.h>.  In particular, the
    # audit token is LOCAL_PEERTOKEN (0x006), not the historical fabricated
    # value that some older fixtures used.
    SOL_LOCAL = getattr(socket, "SOL_LOCAL", 0)
    LOCAL_PEERPID = 0x002
    LOCAL_PEERTOKEN = 0x006

    def __call__(self, connection: socket.socket) -> PeerIdentity:
        if sys.platform != "darwin":
            raise HarnessProtocolError("Darwin kernel peer identity unavailable")
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            getpeereid = libc.getpeereid
            getpeereid.argtypes = [
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint),
                ctypes.POINTER(ctypes.c_uint),
            ]
            getpeereid.restype = ctypes.c_int
            euid = ctypes.c_uint(0)
            egid = ctypes.c_uint(0)
            if getpeereid(connection.fileno(), ctypes.byref(euid), ctypes.byref(egid)) != 0:
                raise OSError(ctypes.get_errno(), "getpeereid failed")
            pid_raw = connection.getsockopt(self.SOL_LOCAL, self.LOCAL_PEERPID, 4)
            token_raw = connection.getsockopt(self.SOL_LOCAL, self.LOCAL_PEERTOKEN, 32)
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            raise HarnessProtocolError("kernel peer identity read failed") from exc
        if not isinstance(pid_raw, (bytes, bytearray)) or len(pid_raw) != 4:
            raise HarnessProtocolError("kernel peer PID has unexpected size")
        if not isinstance(token_raw, (bytes, bytearray)) or len(token_raw) != 32:
            raise HarnessProtocolError("kernel peer audit token has unexpected size")
        pid = int.from_bytes(pid_raw, byteorder=sys.byteorder, signed=False)
        effective_uid = int(euid.value)
        if effective_uid < 0 or pid <= 0:
            raise HarnessProtocolError("kernel peer identity has an invalid value")
        executable_path = self._path_for_pid(pid)
        if not isinstance(executable_path, str) or not executable_path.startswith("/"):
            raise HarnessProtocolError("peer executable path is not absolute")
        return PeerIdentity(effective_uid, pid, bytes(token_raw).hex(), executable_path)

    @staticmethod
    def _path_for_pid(pid: int) -> str:
        try:
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            proc_pidpath = libproc.proc_pidpath
            proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            proc_pidpath.restype = ctypes.c_int
            buffer = ctypes.create_string_buffer(4096)
            length = proc_pidpath(pid, buffer, len(buffer))
            if length <= 0:
                raise OSError("proc_pidpath returned no path")
            return buffer.raw[:length].decode("utf-8")
        except (OSError, UnicodeDecodeError, AttributeError) as exc:
            raise HarnessProtocolError("peer executable path read failed") from exc


@dataclass(frozen=True)
class CaptureTuple:
    kernel_peer: str
    launch_nonce: str
    attempt_id: str
    generation: int


@dataclass(frozen=True)
class Activation:
    tuple: CaptureTuple
    requested_engine: str
    resolved_engine: str
    actual_engine: str

    def is_process_tap(self) -> bool:
        return (
            self.requested_engine == PROCESS_TAP
            and self.resolved_engine == PROCESS_TAP
            and self.actual_engine == PROCESS_TAP
        )


@dataclass(frozen=True)
class PositiveProcessTapProof:
    """The immutable, exact proof object admitted by the PASS boundary."""

    artifact_valid: bool
    current_peer: bool
    authenticated_peer_key: str
    launch_nonce: str
    activation: Activation
    functional_permission_state: str
    functional_permission_tuple: CaptureTuple
    transcript_valid: bool


@dataclass(frozen=True)
class ActivationIdentity:
    """Every field that a later health event must repeat exactly."""

    peer: PeerIdentity
    session_binding: str
    launch_nonce: str
    attempt_id: str
    generation: int
    source_binding: str
    observer_binding: str
    requested_engine: str
    resolved_engine: str
    actual_engine: str


class CFDictionaryKeyCallBacks(ctypes.Structure):
    """Darwin CoreFoundation ``CFDictionaryKeyCallBacks`` ABI shape."""

    _fields_ = [
        ("version", ctypes.c_long),
        ("retain", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("copyDescription", ctypes.c_void_p),
        ("equal", ctypes.c_void_p),
        ("hash", ctypes.c_void_p),
    ]


class CFDictionaryValueCallBacks(ctypes.Structure):
    """Darwin CoreFoundation ``CFDictionaryValueCallBacks`` ABI shape."""

    _fields_ = [
        ("version", ctypes.c_long),
        ("retain", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("copyDescription", ctypes.c_void_p),
        ("equal", ctypes.c_void_p),
    ]


class DarwinSecurityBridge:
    """Small, ownership-audited Security/CoreFoundation ctypes bridge.

    The bridge is deliberately constructed only when a production caller
    asks for it.  Offline tests inject a ``StaticCodeIdentityReader`` and a
    ``RunningCodeAttestor`` and therefore never load Security.framework or
    call ``codesign``.  Every CF object created/copied below is released in
    the reverse order of acquisition; borrowed dictionary values and exported
    constants are read but never released.
    """

    STATIC_VALIDITY_FLAGS = 0x19  # CheckAllArchitectures|CheckNestedCode|StrictValidate
    GUEST_REQUIREMENT_FLAGS = 1 << 23  # kSecCSMatchGuestRequirementInKernel
    REQUIREMENT_INFORMATION_FLAGS = 1 << 2  # kSecCSRequirementInformation
    PROPERTY_LIST_XML_FORMAT = 100  # kCFPropertyListXMLFormat_v1_0

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise HarnessProtocolError("Security.framework bridge is Darwin-only")
        try:
            self._security = ctypes.CDLL(
                "/System/Library/Frameworks/Security.framework/Security",
                use_errno=True,
            )
            self._core_foundation = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
                use_errno=True,
            )
            self._bind_functions()
            # NULL is CoreFoundation's documented default allocator.  Do not
            # read kCFAllocatorDefault from the framework: its export is not
            # needed by this ABI bridge and may be represented differently
            # across SDK/runtime versions.
            self._allocator: ctypes.c_void_p | None = None
            # These are exported CF key pointers.  Never recreate an equal
            # CFString in Python: SecCode's dictionary lookup is pointer/API
            # based and the keys themselves are unowned framework constants.
            self._unique_key = self._export_pointer(
                self._security, "kSecCodeInfoUnique"
            )
            self._lwcr_key = self._export_pointer(
                self._security, "kSecCodeInfoDefaultDesignatedLightweightCodeRequirement"
            )
            self._guest_audit_key = self._export_pointer(
                self._security, "kSecGuestAttributeAudit"
            )
            # CoreFoundation exports these as callback *structures*, not
            # CFTypeRef pointer constants.  ``in_dll`` reads the structure at
            # the symbol address and the instances are retained for the
            # lifetime of the bridge so the pointers passed to
            # CFDictionaryCreate remain valid.
            self._cf_type_dictionary_key_callbacks = CFDictionaryKeyCallBacks.in_dll(
                self._core_foundation, "kCFTypeDictionaryKeyCallBacks"
            )
            self._cf_type_dictionary_value_callbacks = CFDictionaryValueCallBacks.in_dll(
                self._core_foundation, "kCFTypeDictionaryValueCallBacks"
            )
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            raise HarnessProtocolError("Security.framework bridge is unavailable") from exc

    @staticmethod
    def _export_pointer(library: ctypes.CDLL, symbol: str) -> ctypes.c_void_p:
        pointer = ctypes.c_void_p.in_dll(library, symbol)
        if not pointer.value:
            raise HarnessProtocolError(f"Security export is null: {symbol}")
        return pointer

    def _bind_functions(self) -> None:
        cf = self._core_foundation
        security = self._security

        def bind(
            library: ctypes.CDLL,
            name: str,
            argtypes: list[Any],
            restype: Any,
        ) -> Any:
            function = getattr(library, name)
            function.argtypes = argtypes
            function.restype = restype
            return function

        self._cf_release = bind(cf, "CFRelease", [ctypes.c_void_p], None)
        self._cf_get_type_id = bind(cf, "CFGetTypeID", [ctypes.c_void_p], ctypes.c_ulong)
        self._cf_data_get_type_id = bind(
            cf, "CFDataGetTypeID", [], ctypes.c_ulong
        )
        self._cf_data_create = bind(
            cf,
            "CFDataCreate",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_long],
            ctypes.c_void_p,
        )
        self._cf_data_get_length = bind(
            cf, "CFDataGetLength", [ctypes.c_void_p], ctypes.c_long
        )
        self._cf_data_get_byte_ptr = bind(
            cf,
            "CFDataGetBytePtr",
            [ctypes.c_void_p],
            ctypes.POINTER(ctypes.c_ubyte),
        )
        self._cf_dictionary_get_type_id = bind(
            cf, "CFDictionaryGetTypeID", [], ctypes.c_ulong
        )
        self._cf_dictionary_create = bind(
            cf,
            "CFDictionaryCreate",
            [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_long,
                ctypes.POINTER(CFDictionaryKeyCallBacks),
                ctypes.POINTER(CFDictionaryValueCallBacks),
            ],
            ctypes.c_void_p,
        )
        self._cf_dictionary_get_value = bind(
            cf,
            "CFDictionaryGetValue",
            [ctypes.c_void_p, ctypes.c_void_p],
            ctypes.c_void_p,
        )
        self._cf_url_create = bind(
            cf,
            "CFURLCreateFromFileSystemRepresentation",
            [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ubyte),
                ctypes.c_long,
                # CoreFoundation's Darwin ``Boolean`` is ``unsigned char``;
                # do not model it as C ``_Bool`` in this ABI bridge.
                ctypes.c_ubyte,
            ],
            ctypes.c_void_p,
        )
        self._sec_static_create = bind(
            security,
            "SecStaticCodeCreateWithPath",
            [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.c_int32,
        )
        self._sec_static_check = bind(
            security,
            "SecStaticCodeCheckValidity",
            [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p],
            ctypes.c_int32,
        )
        self._sec_copy_signing = bind(
            security,
            "SecCodeCopySigningInformation",
            [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.c_int32,
        )
        self._sec_copy_designated = bind(
            security,
            "SecCodeCopyDesignatedRequirement",
            [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.c_int32,
        )
        self._sec_requirement_copy_data = bind(
            security,
            "SecRequirementCopyData",
            [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.c_int32,
        )
        self._sec_requirement_create_data = bind(
            security,
            "SecRequirementCreateWithData",
            [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.c_int32,
        )
        self._sec_requirement_create_lwcr = bind(
            security,
            "SecRequirementCreateWithLightweightCodeRequirementData",
            [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p),
            ],
            ctypes.c_int32,
        )
        self._cf_property_list_create_data = bind(
            cf,
            "CFPropertyListCreateData",
            [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_long,
                ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_void_p),
            ],
            ctypes.c_void_p,
        )
        self._sec_copy_guest = bind(
            security,
            "SecCodeCopyGuestWithAttributes",
            [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.c_int32,
        )
        self._sec_check_validity = bind(
            security,
            "SecCodeCheckValidity",
            [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p],
            ctypes.c_int32,
        )

    def _release(self, value: ctypes.c_void_p | None) -> None:
        if value is not None and value.value:
            self._cf_release(value)

    def _copy_cfdata(self, value: ctypes.c_void_p, *, maximum: int, label: str) -> bytes:
        if not value or not value.value:
            raise HarnessProtocolError(f"{label} CFData is null")
        try:
            type_id = self._cf_get_type_id(value)
            data_type_id = self._cf_data_get_type_id()
        except (OSError, TypeError, ValueError) as exc:
            raise HarnessProtocolError(f"{label} CFData type read failed") from exc
        if type_id != data_type_id:
            raise HarnessProtocolError(f"{label} is not CFData")
        length = int(self._cf_data_get_length(value))
        if length < 1 or length > maximum:
            raise HarnessProtocolError(f"{label} CFData length is outside bounds")
        pointer = self._cf_data_get_byte_ptr(value)
        if not pointer:
            raise HarnessProtocolError(f"{label} CFData bytes are null")
        return bytes(ctypes.string_at(pointer, length))

    def _require_cf_type(
        self,
        value: ctypes.c_void_p,
        expected_type_id: int,
        label: str,
    ) -> None:
        if not value or not value.value:
            raise HarnessProtocolError(f"{label} CF object is null")
        try:
            actual_type_id = int(self._cf_get_type_id(value))
        except (OSError, TypeError, ValueError) as exc:
            raise HarnessProtocolError(f"{label} CF type read failed") from exc
        if actual_type_id != int(expected_type_id):
            raise HarnessProtocolError(f"{label} has the wrong CF type")

    def _copy_lwcr_facts_dictionary(self, value: ctypes.c_void_p) -> dict[str, object]:
        error: ctypes.c_void_p | None = ctypes.c_void_p()
        plist_data: ctypes.c_void_p | None = None
        try:
            plist_data = ctypes.c_void_p(
                self._cf_property_list_create_data(
                    self._allocator,
                    value,
                    self.PROPERTY_LIST_XML_FORMAT,
                    0,
                    ctypes.byref(error),
                )
            )
            if not plist_data.value:
                raise HarnessProtocolError("static lightweight requirement dictionary is unreadable")
            raw = self._copy_cfdata(
                plist_data,
                maximum=65_536,
                label="static lightweight requirement dictionary",
            )
            facts = plistlib.loads(raw)
            if type(facts) is not dict or not facts:
                raise HarnessProtocolError("static lightweight requirement dictionary is empty")
            return facts
        except (OSError, TypeError, ValueError, plistlib.InvalidFileException) as exc:
            raise HarnessProtocolError("static lightweight requirement dictionary parse failed") from exc
        finally:
            self._release(plist_data)
            self._release(error)

    def _requirement_blob_from_lwcr_der(self, der: bytes) -> bytes:
        if type(der) is not bytes or not 1 <= len(der) <= 65_536:
            raise HarnessProtocolError("lightweight requirement DER is outside bounds")
        requirement_data: ctypes.c_void_p | None = None
        requirement: ctypes.c_void_p | None = None
        copied: ctypes.c_void_p | None = None
        error: ctypes.c_void_p | None = ctypes.c_void_p()
        try:
            buffer = (ctypes.c_ubyte * len(der)).from_buffer_copy(der)
            created = self._cf_data_create(self._allocator, buffer, len(der))
            if not created:
                raise HarnessProtocolError("lightweight requirement CFData creation failed")
            requirement_data = ctypes.c_void_p(created)
            if len(self._copy_cfdata(requirement_data, maximum=65_536, label="lightweight requirement DER")) != len(der):
                raise HarnessProtocolError("lightweight requirement DER length changed")
            requirement = ctypes.c_void_p()
            status = self._sec_requirement_create_lwcr(
                requirement_data,
                0,
                ctypes.byref(requirement),
                ctypes.byref(error),
            )
            if status != 0 or not requirement.value:
                raise HarnessProtocolError("SecRequirementCreateWithLightweightCodeRequirementData failed")
            copied = ctypes.c_void_p()
            status = self._sec_requirement_copy_data(requirement, 0, ctypes.byref(copied))
            if status != 0 or not copied.value:
                raise HarnessProtocolError("lightweight requirement data is unavailable")
            return self._copy_cfdata(
                copied,
                maximum=65_536,
                label="static lightweight requirement",
            )
        except HarnessProtocolError:
            raise
        except (OSError, TypeError, ValueError, OverflowError) as exc:
            raise HarnessProtocolError("lightweight requirement blob creation failed") from exc
        finally:
            self._release(copied)
            self._release(requirement)
            self._release(requirement_data)
            self._release(error)

    def _app_url(self, app_path: Path) -> ctypes.c_void_p:
        path = Path(app_path)
        if not path.is_absolute():
            raise HarnessProtocolError("static app path must be absolute")
        encoded = os.fsencode(str(path))
        if not encoded or b"\0" in encoded:
            raise HarnessProtocolError("static app path is malformed")
        bytes_buffer = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        url = self._cf_url_create(
            self._allocator,
            bytes_buffer,
            len(encoded),
            True,
        )
        if not url:
            raise HarnessProtocolError("static app CFURL creation failed")
        return ctypes.c_void_p(url)

    def read_static_identity(self, app_path: Path) -> StaticCodeIdentity:
        """Read static unique CDHash and designated requirement after validity."""

        url: ctypes.c_void_p | None = None
        static_code: ctypes.c_void_p | None = None
        signing_info: ctypes.c_void_p | None = None
        designated_requirement: ctypes.c_void_p | None = None
        requirement_data: ctypes.c_void_p | None = None
        lightweight_data: ctypes.c_void_p | None = None
        try:
            url = self._app_url(app_path)
            static_code = ctypes.c_void_p()
            status = self._sec_static_create(
                url,
                0,
                ctypes.byref(static_code),
            )
            if status != 0 or not static_code.value:
                raise HarnessProtocolError("SecStaticCodeCreateWithPath failed")
            # The nil requirement is intentional for the static policy
            # validity check; the dynamic path below never uses a nil one.
            status = self._sec_static_check(
                static_code,
                self.STATIC_VALIDITY_FLAGS,
                None,
            )
            if status != 0:
                raise HarnessProtocolError("SecStaticCodeCheckValidity failed")
            signing_info = ctypes.c_void_p()
            status = self._sec_copy_signing(
                static_code,
                self.REQUIREMENT_INFORMATION_FLAGS,
                ctypes.byref(signing_info),
            )
            if status != 0 or not signing_info.value:
                raise HarnessProtocolError("static signing information is unavailable")
            self._require_cf_type(
                signing_info,
                self._cf_dictionary_get_type_id(),
                "static signing information",
            )
            unique_data = self._cf_dictionary_get_value(signing_info, self._unique_key)
            unique_cdhash = self._copy_cfdata(
                ctypes.c_void_p(unique_data), maximum=64, label="static unique"
            )
            designated_requirement = ctypes.c_void_p()
            status = self._sec_copy_designated(
                static_code,
                0,
                ctypes.byref(designated_requirement),
            )
            if status != 0 or not designated_requirement.value:
                raise HarnessProtocolError("static designated requirement is unavailable")
            requirement_data = ctypes.c_void_p()
            status = self._sec_requirement_copy_data(
                designated_requirement,
                0,
                ctypes.byref(requirement_data),
            )
            if status != 0 or not requirement_data.value:
                raise HarnessProtocolError("static designated requirement data is unavailable")
            requirement = self._copy_cfdata(
                requirement_data,
                maximum=65_536,
                label="static designated requirement",
            )
            lwcr_requirement = self._cf_dictionary_get_value(signing_info, self._lwcr_key)
            if not lwcr_requirement:
                raise HarnessProtocolError("static lightweight requirement is unavailable")
            lwcr_ref = ctypes.c_void_p(lwcr_requirement)
            try:
                lwcr_type_id = int(self._cf_get_type_id(lwcr_ref))
            except (OSError, TypeError, ValueError) as exc:
                raise HarnessProtocolError("static lightweight requirement type read failed") from exc
            if lwcr_type_id == int(self._cf_dictionary_get_type_id()):
                lightweight = self._requirement_blob_from_lwcr_der(
                    encode_lightweight_code_requirement(self._copy_lwcr_facts_dictionary(lwcr_ref))
                )
            else:
                lightweight_data = ctypes.c_void_p()
                status = self._sec_requirement_copy_data(
                    lwcr_ref,
                    0,
                    ctypes.byref(lightweight_data),
                )
                if status != 0 or not lightweight_data.value:
                    raise HarnessProtocolError("static lightweight requirement data is unavailable")
                lightweight = self._copy_cfdata(
                    lightweight_data,
                    maximum=65_536,
                    label="static lightweight requirement",
                )
            return StaticCodeIdentity(
                unique_cdhash=unique_cdhash,
                designated_requirement=requirement,
                lightweight_requirement=lightweight,
            )
        except HarnessProtocolError:
            raise
        except (OSError, AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise HarnessProtocolError("static Security.framework identity read failed") from exc
        finally:
            # Reverse the Create/Copy acquisition order.  Dictionary unique
            # and lightweight-requirement values are borrowed.
            self._release(lightweight_data)
            self._release(requirement_data)
            self._release(designated_requirement)
            self._release(signing_info)
            self._release(static_code)
            self._release(url)

    def attest_running_code(
        self,
        peer: PeerIdentity,
        expected: StaticCodeIdentity,
    ) -> StaticCodeIdentity:
        """Bind one accepted audit token to a valid dynamic SecCode."""

        if type(peer) is not PeerIdentity:
            raise HarnessProtocolError("dynamic peer identity is not exact")
        require_exact_static_code_identity(expected, label="expected static identity")
        token = _audit_token_bytes(peer.audit_token)
        audit_data: ctypes.c_void_p | None = None
        attributes: ctypes.c_void_p | None = None
        requirement_data: ctypes.c_void_p | None = None
        requirement: ctypes.c_void_p | None = None
        guest: ctypes.c_void_p | None = None
        signing_info: ctypes.c_void_p | None = None
        try:
            token_buffer = (ctypes.c_ubyte * len(token)).from_buffer_copy(token)
            audit_data_raw = self._cf_data_create(
                self._allocator,
                token_buffer,
                len(token),
            )
            if not audit_data_raw:
                raise HarnessProtocolError("audit-token CFData creation failed")
            audit_data = ctypes.c_void_p(audit_data_raw)
            if len(self._copy_cfdata(audit_data, maximum=32, label="audit token")) != 32:
                raise HarnessProtocolError("audit-token CFData length is not 32 bytes")
            key = self._guest_audit_key
            value = audit_data
            keys = (ctypes.c_void_p * 1)(key.value)
            values = (ctypes.c_void_p * 1)(value.value)
            attributes_raw = self._cf_dictionary_create(
                self._allocator,
                keys,
                values,
                1,
                ctypes.byref(self._cf_type_dictionary_key_callbacks),
                ctypes.byref(self._cf_type_dictionary_value_callbacks),
            )
            if not attributes_raw:
                raise HarnessProtocolError("guest attributes dictionary creation failed")
            attributes = ctypes.c_void_p(attributes_raw)
            self._require_cf_type(
                attributes,
                self._cf_dictionary_get_type_id(),
                "guest attributes",
            )
            requirement_buffer = (ctypes.c_ubyte * len(expected.lightweight_requirement)).from_buffer_copy(
                expected.lightweight_requirement
            )
            requirement_data_raw = self._cf_data_create(
                self._allocator,
                requirement_buffer,
                len(expected.lightweight_requirement),
            )
            if not requirement_data_raw:
                raise HarnessProtocolError("lightweight requirement CFData creation failed")
            requirement_data = ctypes.c_void_p(requirement_data_raw)
            if len(
                self._copy_cfdata(
                    requirement_data,
                    maximum=65_536,
                    label="lightweight requirement",
                )
            ) != len(expected.lightweight_requirement):
                raise HarnessProtocolError("lightweight requirement CFData length changed")
            requirement = ctypes.c_void_p()
            status = self._sec_requirement_create_data(
                requirement_data,
                0,
                ctypes.byref(requirement),
            )
            if status != 0 or not requirement.value:
                raise HarnessProtocolError("SecRequirementCreateWithData failed")
            guest = ctypes.c_void_p()
            status = self._sec_copy_guest(
                None,
                attributes,
                0,
                ctypes.byref(guest),
            )
            if status != 0 or not guest.value:
                raise HarnessProtocolError("SecCodeCopyGuestWithAttributes failed")
            status = self._sec_check_validity(
                guest,
                self.GUEST_REQUIREMENT_FLAGS,
                requirement,
            )
            if status != 0:
                raise HarnessProtocolError("dynamic SecCode validity check failed")
            signing_info = ctypes.c_void_p()
            status = self._sec_copy_signing(
                guest,
                0,
                ctypes.byref(signing_info),
            )
            if status != 0 or not signing_info.value:
                raise HarnessProtocolError("dynamic signing information is unavailable")
            self._require_cf_type(
                signing_info,
                self._cf_dictionary_get_type_id(),
                "dynamic signing information",
            )
            unique_data = self._cf_dictionary_get_value(signing_info, self._unique_key)
            dynamic_unique = self._copy_cfdata(
                ctypes.c_void_p(unique_data), maximum=64, label="dynamic unique"
            )
            if not hmac.compare_digest(dynamic_unique, expected.unique_cdhash):
                raise HarnessProtocolError("dynamic unique code hash mismatch")
            # Revalidate after extracting the borrowed signing-info value; a
            # guest code that changes identity at this edge cannot pass.
            status = self._sec_check_validity(
                guest,
                self.GUEST_REQUIREMENT_FLAGS,
                requirement,
            )
            if status != 0:
                raise HarnessProtocolError("dynamic SecCode revalidation failed")
            return StaticCodeIdentity(
                unique_cdhash=dynamic_unique,
                designated_requirement=expected.designated_requirement,
                lightweight_requirement=expected.lightweight_requirement,
            )
        except HarnessProtocolError:
            raise
        except (OSError, AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise HarnessProtocolError("dynamic Security.framework attestation failed") from exc
        finally:
            self._release(signing_info)
            self._release(guest)
            self._release(requirement)
            self._release(requirement_data)
            self._release(attributes)
            self._release(audit_data)


class DarwinStaticCodeIdentityReader:
    """Production static reader; tests inject a fake callable instead."""

    def __init__(self, bridge: DarwinSecurityBridge | None = None) -> None:
        self._bridge = bridge

    def __call__(self, app_path: Path) -> StaticCodeIdentity:
        bridge = self._bridge or DarwinSecurityBridge()
        identity = bridge.read_static_identity(Path(app_path))
        return require_exact_static_code_identity(
            identity,
            label="static identity reader result",
        )


class RunningCodeAttestor(Protocol):
    """Required dynamic code identity boundary retained by one CompanionRun."""

    def __call__(
        self,
        peer: PeerIdentity,
        expected: StaticCodeIdentity,
    ) -> StaticCodeIdentity:
        ...


class DarwinRunningCodeAttestor:
    """Production dynamic audit-token guest-code attestor."""

    def __init__(self, bridge: DarwinSecurityBridge | None = None) -> None:
        self._bridge = bridge

    def __call__(
        self,
        peer: PeerIdentity,
        expected: StaticCodeIdentity,
    ) -> StaticCodeIdentity:
        bridge = self._bridge or DarwinSecurityBridge()
        result = bridge.attest_running_code(peer, expected)
        return require_exact_static_code_identity(
            result,
            expected,
            label="dynamic attestor result",
        )


@dataclass(frozen=True)
class ArtifactFacts:
    provenance_head: str
    provenance_tree: str
    dirty: bool
    bundle_id: str
    team_id: str
    hardened_runtime: bool
    entitlements: tuple[str, ...]
    strict_signature: bool
    executable_digest: str
    static_identity: StaticCodeIdentity
    sealed_executable_digest: str = ""
    provenance_digest: str = ""
    developer_id_authority: bool = False
    audio_input_entitlement: bool = False

    @property
    def static_code_identity(self) -> StaticCodeIdentity:
        """Compatibility spelling for callers that use the contract name."""

        return self.static_identity


REQUIRED_ENTITLEMENTS = ("com.apple.security.device.audio-input",)


def artifact_provenance_payload(facts: ArtifactFacts) -> dict[str, Any]:
    return {
        "dirty": facts.dirty,
        "executable_sha256": facts.executable_digest,
        "head": facts.provenance_head,
        "tree": facts.provenance_tree,
    }


def artifact_provenance_digest(facts: ArtifactFacts) -> str:
    return hashlib.sha256(canonical_json(artifact_provenance_payload(facts))).hexdigest()


def validate_artifact(
    facts: ArtifactFacts,
    *,
    expected_head: str,
    expected_tree: str,
    expected_digest: str | None = None,
) -> None:
    """Fail closed on sealed provenance, signature policy, and exact digest."""

    if type(facts) is not ArtifactFacts:
        raise HarnessProtocolError("artifact facts must be an exact ArtifactFacts")
    static_identity = getattr(facts, "static_identity", None)
    require_exact_static_code_identity(
        static_identity,
        label="artifact static identity",
    )

    checks: tuple[tuple[bool, str], ...] = (
        (not facts.dirty, "artifact working tree is dirty"),
        (_HEX40.fullmatch(facts.provenance_head) is not None, "artifact HEAD provenance is malformed"),
        (_HEX40.fullmatch(facts.provenance_tree) is not None, "artifact tree provenance is malformed"),
        (facts.provenance_head == expected_head, "artifact HEAD provenance mismatch"),
        (facts.provenance_tree == expected_tree, "artifact tree provenance mismatch"),
        (facts.bundle_id == "com.ellaexecutivesearch.tarscompanion", "bundle identifier mismatch"),
        (facts.team_id == "3FLG8W6B95", "team identifier mismatch"),
        (facts.hardened_runtime, "hardened runtime is absent"),
        (facts.strict_signature, "strict signature verification failed"),
        (_HEX64.fullmatch(facts.executable_digest) is not None, "executable digest is malformed"),
        (
            _HEX64.fullmatch(facts.sealed_executable_digest) is not None
            and facts.sealed_executable_digest == facts.executable_digest,
            "sealed executable digest mismatch",
        ),
        (
            _HEX64.fullmatch(facts.provenance_digest) is not None
            and facts.provenance_digest == artifact_provenance_digest(facts),
            "sealed provenance digest mismatch",
        ),
        (
            facts.developer_id_authority is True,
            "Developer ID Application authority is absent",
        ),
        (
            facts.audio_input_entitlement is True,
            "audio-input entitlement is not enabled",
        ),
        (expected_digest is None or facts.executable_digest == expected_digest, "executable digest mismatch"),
    )
    checks += (
        (
            tuple(sorted(facts.entitlements)) == REQUIRED_ENTITLEMENTS,
            "entitlements differ from the exact Task 11 allowlist",
        ),
    )
    for passed, message in checks:
        if not passed:
            raise HarnessProtocolError(message)


@dataclass(frozen=True)
class LaunchSpec:
    """LaunchServices data, not a subprocess argv for a CLI executable."""

    app_path: str
    argv: tuple[str, ...]

    @property
    def launch_arguments(self) -> tuple[str, ...]:
        """Arguments passed through LaunchServices, excluding the app URL."""

        return self.argv[1:]

    @property
    def executable_path(self) -> str:
        """The exact executable path the kernel peer reader must report."""

        return str(Path(self.app_path) / "Contents" / "MacOS" / "TarsCompanionApp")


def make_launch_spec(
    app_path: str | os.PathLike[str],
    *,
    socket_path: str,
    launch_nonce: str,
    stream_key: str,
) -> LaunchSpec:
    path = str(Path(app_path))
    if not path.endswith(".app") or not os.path.isabs(path):
        raise HarnessProtocolError("live harness requires an explicit absolute .app path")
    if not socket_path.startswith("/") or "\0" in socket_path or not _IDENTIFIER.fullmatch(launch_nonce):
        raise HarnessProtocolError("invalid control path or launch nonce")
    validate_stream_key(stream_key)
    # ``argv`` is retained as a LaunchServices argument specification for the
    # injected adapter.  It is never passed to subprocess.Popen directly.
    argv = (
        path,
        "--live-harness-socket",
        socket_path,
        "--live-harness-nonce",
        launch_nonce,
        "--system-audio-engine",
        PROCESS_TAP,
    )
    if any(stream_key in argument for argument in argv):
        raise HarnessProtocolError("active stream key entered prelaunch arguments or path")
    forbidden = ("tars-companion", ".build", "swift", "--stream-key", "--key", "--cli")
    if any(any(token in arg for token in forbidden) for arg in argv[1:]):
        raise HarnessProtocolError("launch spec contains CLI/build/fallback argument")
    return LaunchSpec(app_path=path, argv=argv)


class LaunchServicesAdapter(Protocol):
    """Injected app-launch boundary; no direct .app Popen fallback exists."""

    def launch(
        self,
        spec: LaunchSpec,
        *,
        on_process: Callable[[Any], None],
    ) -> Any:
        ...


@dataclass(frozen=True)
class LaunchResult:
    """A retained LaunchServices process facade plus pre-accept constraints."""

    process: Any
    peer: PeerIdentity


def _audit_token_bytes(value: object) -> bytes:
    """Decode exactly one Darwin ``audit_token_t`` without lossy coercion."""

    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise HarnessProtocolError("authenticated peer audit token is not exactly 32-byte hex")
    try:
        token = bytes.fromhex(value)
    except ValueError as exc:
        raise HarnessProtocolError("authenticated peer audit token is not valid hex") from exc
    if len(token) != 32:
        raise HarnessProtocolError("authenticated peer audit token has unexpected size")
    return token


class DarwinAuditToken(ctypes.Structure):
    """The SDK audit-token layout: eight native 32-bit words (32 bytes)."""

    _fields_ = [("val", ctypes.c_uint32 * 8)]


class DarwinAuditTokenSignalSender:
    """Perform the destructive operation through libproc's token boundary.

    ``proc_signal_with_audittoken`` deliberately has no PID argument: the
    kernel extracts the target process and its pid-version from the immutable
    audit token.  Keeping this callback token-only prevents a checked integer
    PID from becoming a time-of-check/time-of-use authorization boundary.
    """

    def __call__(self, audit_token: bytes, signum: int) -> None:
        if sys.platform != "darwin":
            raise HarnessProtocolError("audit-token signaling is Darwin-only")
        if not isinstance(audit_token, bytes) or len(audit_token) != 32:
            raise HarnessProtocolError("audit token is not exactly 32 immutable bytes")
        if not isinstance(signum, int) or signum <= 0:
            raise HarnessProtocolError("signal number is invalid")
        if ctypes.sizeof(DarwinAuditToken) != 32:
            raise HarnessProtocolError("audit token ABI is not exactly 32 bytes")
        try:
            # ``bytes`` is immutable and the copy is retained only for the
            # duration of this one kernel call.
            token = DarwinAuditToken.from_buffer_copy(audit_token)
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            proc_signal = getattr(libproc, "proc_signal_with_audittoken")
            proc_signal.argtypes = [
                ctypes.POINTER(DarwinAuditToken),
                ctypes.c_int32,
            ]
            proc_signal.restype = ctypes.c_int
            result = proc_signal(ctypes.byref(token), signum)
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            raise HarnessProtocolError("Darwin audit-token signal boundary unavailable") from exc
        if result != 0:
            raise HarnessProtocolError("Darwin audit-token signal was rejected")


class LaunchServicesProcess:
    """Process facade with separate helper and authenticated-peer identities.

    ``/usr/bin/open -W`` is a child helper, not the companion app.  Before
    peer admission only that helper may be observed or terminated for failed
    launch cleanup.  Once the accepted descriptor is authenticated, every
    lifecycle operation first revalidates the immutable full peer identity on
    that still-open descriptor and checks that the ``open -W`` helper remains
    alive.  Only then may it observe or signal the peer PID.
    """

    def __init__(
        self,
        helper: Any,
        *,
        signal_sender: Callable[[bytes, int], int | None] | None = None,
        helper_liveness: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        helper_pid = getattr(helper, "pid", None)
        if not isinstance(helper_pid, int) or helper_pid <= 0:
            raise HarnessProtocolError("LaunchServices helper has no valid PID")
        self._helper = helper
        self._helper_pid = helper_pid
        self._authenticated_pid: int | None = None
        self._authenticated_peer: PeerIdentity | None = None
        self._peer_revalidator: Callable[[], PeerIdentity] | None = None
        self._helper_liveness = helper_liveness or self._default_helper_liveness
        self._signal_sender = signal_sender or DarwinAuditTokenSignalSender()
        self._clock = clock
        self._sleeper = sleeper
        self._last_authenticated_signal: int | None = None
        self.stdout = getattr(helper, "stdout", None)

    @property
    def helper_pid(self) -> int:
        """The ``open`` helper PID, usable only for pre-bind cleanup."""

        return self._helper_pid

    @property
    def pid(self) -> int | None:
        """Only the authenticated accepted-peer PID is exposed as app PID."""

        return self._authenticated_pid

    @property
    def authenticated_pid(self) -> int | None:
        return self._authenticated_pid

    @property
    def authenticated_peer(self) -> PeerIdentity | None:
        """The immutable full identity captured at socket admission."""

        return self._authenticated_peer

    def _default_helper_liveness(self) -> bool:
        try:
            return self._helper.poll() is None
        except (OSError, AttributeError, TypeError):
            return False

    def bind_authenticated_peer(
        self,
        peer: PeerIdentity,
        *,
        revalidator: Callable[[], PeerIdentity],
        helper_liveness: Callable[[], bool] | None = None,
    ) -> None:
        """Bind the process to a complete peer and its live socket revalidator.

        The helper PID is intentionally never accepted as an app identity.
        Binding performs an immediate readback so a stale/injected identity
        cannot become authoritative merely because it was returned once.
        """

        try:
            peer_fingerprint(peer)
        except (AttributeError, TypeError) as exc:
            raise HarnessProtocolError("accepted kernel peer identity is invalid") from exc
        if peer.pid is None or peer.pid <= 0:
            raise HarnessProtocolError("accepted kernel peer has no valid PID")
        if peer.pid == self._helper_pid:
            raise HarnessProtocolError("LaunchServices helper PID cannot authenticate the app")
        _audit_token_bytes(peer.audit_token)
        if not callable(revalidator):
            raise HarnessProtocolError("authenticated peer revalidator is required")
        if self._authenticated_peer is not None and not peer_identity_equal(self._authenticated_peer, peer):
            raise HarnessProtocolError("authenticated peer identity cannot change")
        if helper_liveness is not None:
            if not callable(helper_liveness):
                raise HarnessProtocolError("helper liveness probe is invalid")
            self._helper_liveness = helper_liveness
        try:
            current = revalidator()
        except Exception as exc:
            raise HarnessProtocolError("authenticated peer revalidation failed") from exc
        if not peer_identity_equal(current, peer):
            raise HarnessProtocolError("authenticated peer identity changed during binding")
        if not self._helper_is_alive():
            raise HarnessProtocolError("LaunchServices open helper exited before peer binding")
        _audit_token_bytes(current.audit_token)
        self._authenticated_peer = peer
        self._authenticated_pid = peer.pid
        self._peer_revalidator = revalidator

    def bind_authenticated_pid(self, pid: int | None) -> None:
        """Reject the old PID-only API so callers cannot bypass peer binding."""

        _ = pid
        raise HarnessProtocolError("full authenticated peer and revalidator are required")

    def _helper_is_alive(self) -> bool:
        try:
            return bool(self._helper_liveness())
        except Exception:
            return False

    def _revalidate_peer(self) -> PeerIdentity:
        peer = self._authenticated_peer
        revalidator = self._peer_revalidator
        if peer is None or revalidator is None or self._authenticated_pid is None:
            raise HarnessProtocolError("authenticated peer is not bound")
        try:
            current = revalidator()
        except Exception as exc:
            raise HarnessProtocolError("authenticated peer revalidation failed") from exc
        if not peer_identity_equal(current, peer):
            raise HarnessProtocolError("authenticated peer identity changed")
        return peer

    def _authorize_authenticated_operation(self) -> bytes:
        peer = self._revalidate_peer()
        if not self._helper_is_alive():
            raise HarnessProtocolError("LaunchServices open helper exited")
        return _audit_token_bytes(peer.audit_token)

    def poll(self) -> int | None:
        if self._authenticated_peer is None:
            return self._helper.poll()
        self._authorize_authenticated_operation()
        return None

    def wait(self, timeout: float | None = None) -> int:
        if self._authenticated_peer is None:
            return self._helper.wait(timeout=timeout)
        deadline = None if timeout is None else self._clock() + timeout
        while True:
            try:
                status = self.poll()
            except HarnessProtocolError:
                # Once the exact token boundary has accepted a lifecycle
                # signal, disappearance of the LaunchServices helper is the
                # completion edge.  SIGKILL can make peer revalidation fail a
                # few milliseconds before ``open -W`` exits; keep polling
                # until that helper death or the wait deadline.  Identity
                # failures with no prior token signal remain errors.
                if self._last_authenticated_signal is not None and not self._helper_is_alive():
                    return 0
                if self._last_authenticated_signal is not None:
                    if deadline is not None and self._clock() >= deadline:
                        raise TimeoutError("LaunchServices app wait timed out")
                    self._sleeper(0.05)
                    continue
                raise
            if status is not None:
                return status
            if deadline is not None and self._clock() >= deadline:
                raise TimeoutError("LaunchServices app wait timed out")
            self._sleeper(0.05)

    def wait_for_helper_completion(self, timeout: float | None = None) -> int:
        """Observe the retained ``open -W`` helper without signaling a peer.

        Once the authenticated app has closed its control socket, its audit
        token is no longer available for revalidation or signaling.  The
        LaunchServices helper is the only remaining completion edge in that
        case.  Keep this observation separate from :meth:`wait`, whose
        authenticated-peer contract intentionally rejects a dead descriptor.
        """

        try:
            status = self._helper.poll()
        except (OSError, AttributeError, TypeError) as exc:
            raise HarnessProtocolError("LaunchServices helper completion could not be observed") from exc
        if status is not None:
            return int(status)
        if timeout is not None and timeout <= 0:
            raise TimeoutError("LaunchServices helper completion was not observed")
        try:
            self._helper.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, TimeoutError, OSError, AttributeError, TypeError) as exc:
            raise TimeoutError("LaunchServices helper completion timed out") from exc
        # Some injected process facades return from wait() without actually
        # transitioning to an exited state.  Require a second poll so a
        # fixture cannot turn an alive helper into a completion claim.
        try:
            status = self._helper.poll()
        except (OSError, AttributeError, TypeError) as exc:
            raise HarnessProtocolError("LaunchServices helper completion could not be observed") from exc
        if status is None:
            raise TimeoutError("LaunchServices helper completion was not observed")
        return int(status)

    def terminate(self) -> None:
        if self._authenticated_peer is None:
            self._helper.terminate()
            return
        token = self._authorize_authenticated_operation()
        self._signal_sender(token, signal.SIGTERM)
        self._last_authenticated_signal = signal.SIGTERM

    def send_signal(self, signum: int) -> None:
        if self._authenticated_peer is None:
            self._helper.send_signal(signum)
            return
        token = self._authorize_authenticated_operation()
        self._signal_sender(token, signum)
        self._last_authenticated_signal = signum

    def kill(self) -> None:
        """Force-stop the helper or authenticated peer through the same boundary."""

        self.send_signal(signal.SIGKILL)


def _production_spawn_helper(
    argv: list[str],
    *,
    on_helper_spawned: Callable[[Any], None],
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.STDOUT,
    **kwargs: Any,
) -> subprocess.Popen:
    if not callable(on_helper_spawned):
        raise HarnessProtocolError("on_helper_spawned callback is required")
    if os.name == "posix" and not hasattr(signal, "pthread_sigmask"):
        raise HarnessProtocolError("signal.pthread_sigmask is unavailable")
    orig_mask = None
    if hasattr(signal, "pthread_sigmask"):
        try:
            orig_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}
            )
        except OSError as exc:
            raise HarnessProtocolError("cannot block SIGINT and SIGTERM") from exc
    helper = None
    try:
        helper = subprocess.Popen(argv, stdout=stdout, stderr=stderr, **kwargs)
        on_helper_spawned(helper)
    except BaseException:
        if orig_mask is not None and hasattr(signal, "pthread_sigmask"):
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, orig_mask)
            except OSError:
                pass
        raise
    else:
        if orig_mask is not None and hasattr(signal, "pthread_sigmask"):
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, orig_mask)
            except OSError as exc:
                raise HarnessProtocolError("cannot restore prior signal mask") from exc
    return helper


def _cleanup_process_target(target: Any) -> None:
    if target is None:
        return
    try:
        if hasattr(target, "terminate"):
            target.terminate()
        elif hasattr(target, "send_signal"):
            target.send_signal(signal.SIGTERM)
    except BaseException:
        pass
    wait_success = False
    if hasattr(target, "wait"):
        try:
            target.wait(timeout=1.0)
            wait_success = True
        except (subprocess.TimeoutExpired, TimeoutError):
            wait_success = False
        except TypeError:
            try:
                target.wait()
                wait_success = True
            except BaseException:
                wait_success = False
        except BaseException:
            wait_success = False
    if not wait_success:
        try:
            if hasattr(target, "kill"):
                target.kill()
            elif hasattr(target, "send_signal"):
                target.send_signal(signal.SIGKILL)
        except BaseException:
            pass
        if hasattr(target, "wait"):
            try:
                target.wait(timeout=1.0)
            except (subprocess.TimeoutExpired, TimeoutError, TypeError, BaseException):
                pass


class MacOSLaunchServicesAdapter:
    """Concrete LaunchServices boundary using the system ``open`` helper.

    The pre-credential threat boundary is exact kernel eUID, the preflighted
    executable path, and the private per-run socket/nonce.  ``open`` receives
    only the explicit app bundle and harness arguments; PID/audit identity is
    collected by the accepted-socket kernel reader after launch, never guessed
    from the helper PID.
    """

    def __init__(
        self,
        helper_spawner: Callable[..., Any] | None = None,
        *,
        signal_sender: Callable[[bytes, int], int | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._spawner = helper_spawner or _production_spawn_helper
        self._injected = helper_spawner is not None
        self._signal_sender = signal_sender
        self._clock = clock
        self._sleeper = sleeper

    @staticmethod
    def _validate_spec(spec: LaunchSpec) -> None:
        if len(spec.argv) != 7:
            raise HarnessProtocolError("LaunchServices spec has unexpected argument count")
        expected = (
            "--live-harness-socket",
            spec.argv[2],
            "--live-harness-nonce",
            spec.argv[4],
            "--system-audio-engine",
            PROCESS_TAP,
        )
        if spec.argv[0] != spec.app_path or spec.launch_arguments != expected:
            raise HarnessProtocolError("LaunchServices spec contains unsafe arguments")
        forbidden = {"swift", ".build", "--stream-key", "--key", "--cli", "tars-companion"}
        if any(value in forbidden or value.startswith("--stream-") for value in spec.launch_arguments):
            raise HarnessProtocolError("LaunchServices spec contains secret/build arguments")

    @staticmethod
    def open_argv(spec: LaunchSpec) -> tuple[str, ...]:
        MacOSLaunchServicesAdapter._validate_spec(spec)
        return (
            "/usr/bin/open",
            "-n",
            "-W",
            spec.app_path,
            "--args",
            *spec.launch_arguments,
        )

    def launch(
        self,
        spec: LaunchSpec,
        *,
        on_process: Callable[[Any], None],
    ) -> LaunchResult:
        argv = self.open_argv(spec)
        if not self._injected and (sys.platform != "darwin" or not os.path.exists("/usr/bin/open")):
            raise HarnessProtocolError("macOS LaunchServices /usr/bin/open is unavailable")
        raw_helper: Any = None
        process: Any = None
        helper: Any = None
        identity_already_cleaned = False

        def on_helper_spawned(h: Any) -> None:
            nonlocal raw_helper
            raw_helper = h

        try:
            try:
                helper = self._spawner(
                    list(argv),
                    on_helper_spawned=on_helper_spawned,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            except OSError as exc:
                raise HarnessProtocolError("LaunchServices open helper failed") from exc

            if raw_helper is None:
                if helper is not None:
                    _cleanup_process_target(helper)
                identity_already_cleaned = True
                raise HarnessProtocolError("LaunchServices helper spawner returned without publishing helper")
            if helper is not raw_helper:
                cleaned_targets: set[int] = set()
                for target in (raw_helper, helper):
                    if target is not None:
                        t_id = id(target)
                        if t_id not in cleaned_targets:
                            cleaned_targets.add(t_id)
                            _cleanup_process_target(target)
                identity_already_cleaned = True
                raise HarnessProtocolError("LaunchServices helper spawner published one object and returned another")

            process = (
                raw_helper
                if isinstance(raw_helper, LaunchServicesProcess)
                else LaunchServicesProcess(
                    raw_helper,
                    signal_sender=self._signal_sender,
                    clock=self._clock,
                    sleeper=self._sleeper,
                )
            )
            on_process(process)
            expected_peer = PeerIdentity(
                euid=os.geteuid(),
                pid=None,
                audit_token=None,
                executable_path=spec.executable_path,
            )
            return LaunchResult(process=process, peer=expected_peer)
        except BaseException:
            if not identity_already_cleaned:
                target = process if process is not None else raw_helper
                _cleanup_process_target(target)
            raise


class PeerReader(Protocol):
    def __call__(self, connection: socket.socket) -> PeerIdentity:
        ...


class HarnessState:
    """Authenticated one-peer/one-session/event state machine."""

    def __init__(self, *, expected_peer: PeerIdentity, server_euid: int, launch_nonce: str) -> None:
        if _PRODUCER_LAUNCH_NONCE.fullmatch(launch_nonce) is None:
            raise HarnessProtocolError("launch nonce is not a producer identifier")
        self.expected_peer = expected_peer
        self.server_euid = server_euid
        self.launch_nonce = launch_nonce
        self.peer: PeerIdentity | None = None
        # Do not retain the decoded command mapping.  Only the exact session
        # identity and the credential needed for event admission live here;
        # both are private and the credential is revoked with control loss.
        # Keep only the one-way session reference after command admission;
        # the decoded raw session_id is never retained by the state machine.
        self._session_binding: str | None = None
        self._stream_key: str | None = None
        self._shutdown_acknowledged = False
        self.control_lost = False
        self._activated_generations: set[int] = set()
        self._activation_identities: dict[int, ActivationIdentity] = {}
        self._highest_generation = 0
        self._terminal_identity: ActivationIdentity | None = None

    @property
    def activation_identities(self) -> Mapping[int, ActivationIdentity]:
        """Read-only view used by evidence/tests; values are immutable."""

        return dict(self._activation_identities)

    def accept_peer(self, peer: PeerIdentity) -> None:
        if self.peer is not None:
            raise HarnessProtocolError("competing or duplicate peer")
        if peer.euid != self.server_euid:
            raise HarnessProtocolError("kernel peer eUID mismatch")
        if not peer_matches(peer, self.expected_peer):
            raise HarnessProtocolError("kernel peer identity mismatch")
        peer_fingerprint(peer)
        self.peer = peer

    def accept_command(self, payload: bytes, *, peer: PeerIdentity) -> dict[str, Any]:
        if self.peer is None or not peer_identity_equal(peer, self.peer):
            raise HarnessProtocolError("command from unauthenticated peer")
        if self.control_lost or self._shutdown_acknowledged:
            raise HarnessProtocolError("command after control terminalization")
        if self._session_binding is not None or self._stream_key is not None:
            raise HarnessProtocolError("duplicate session command")
        command = decode_session_command(payload)
        if command["launch_nonce"] != self.launch_nonce:
            raise HarnessProtocolError("launch nonce mismatch")
        self._session_binding = session_binding(command["session_id"], self.launch_nonce)
        self._stream_key = command["stream_key"]
        return command

    def accept_event(self, payload: bytes, *, peer: PeerIdentity) -> dict[str, Any]:
        if self.control_lost or self.peer is None or not peer_identity_equal(peer, self.peer):
            raise HarnessProtocolError("event from inactive peer")
        if self._terminal_identity is not None:
            raise HarnessProtocolError("event follows terminal health failure")
        if self._session_binding is None or self._stream_key is None:
            raise HarnessProtocolError("event before session command")
        stream_key = self._stream_key
        # The state machine is the first boundary that has both the complete
        # credential and decoded event fields.  Reject the complete sentinel
        # symmetrically before retaining or comparing any event identity.
        event = decode_event(payload, stream_key=stream_key)
        expected_session_binding = self._session_binding
        expected_launch_binding = launch_binding(self.launch_nonce)
        if event["launch_nonce"] != expected_launch_binding or event["session_binding"] != expected_session_binding:
            raise HarnessProtocolError("event session/nonce mismatch")
        generation = event["generation"]
        if generation < self._highest_generation:
            raise HarnessProtocolError("stale event generation")
        if event["kind"] == "activation":
            if generation in self._activated_generations:
                raise HarnessProtocolError("duplicate activation")
            identity = ActivationIdentity(
                peer=peer,
                session_binding=event["session_binding"],
                launch_nonce=event["launch_nonce"],
                attempt_id=event["attempt_id"],
                generation=generation,
                source_binding=event["source_binding"],
                observer_binding=event["observer_binding"],
                requested_engine=event["requested_engine"],
                resolved_engine=event["resolved_engine"],
                actual_engine=event["actual_engine"],
            )
            self._activated_generations.add(generation)
            self._activation_identities[generation] = identity
        else:
            status = event.get("status")
            is_terminal_failure = (
                isinstance(status, Mapping)
                and status.get("kind") == "failed"
            )
            if generation not in self._activated_generations and not is_terminal_failure:
                raise HarnessProtocolError("health event precedes activation")
            if generation not in self._activated_generations and is_terminal_failure:
                # An activation-less failed event is admissible only after the
                # authenticated peer and sole command/session fence above have
                # passed.  Its complete event identity becomes sticky; no later
                # activation or health update can revive the run.
                self._terminal_identity = ActivationIdentity(
                    peer=peer,
                    session_binding=event["session_binding"],
                    launch_nonce=event["launch_nonce"],
                    attempt_id=event["attempt_id"],
                    generation=generation,
                    source_binding=event["source_binding"],
                    observer_binding=event["observer_binding"],
                    requested_engine=event["requested_engine"],
                    resolved_engine=event["resolved_engine"],
                    actual_engine=event["actual_engine"],
                )
                self._highest_generation = max(self._highest_generation, generation)
                return event
            identity = self._activation_identities[generation]
            exact_fields = (
                ("peer", peer, identity.peer),
                ("session_binding", event["session_binding"], identity.session_binding),
                ("launch_nonce", event["launch_nonce"], identity.launch_nonce),
                ("attempt_id", event["attempt_id"], identity.attempt_id),
                ("generation", event["generation"], identity.generation),
                ("source_binding", event["source_binding"], identity.source_binding),
                ("observer_binding", event["observer_binding"], identity.observer_binding),
                ("requested_engine", event["requested_engine"], identity.requested_engine),
                ("resolved_engine", event["resolved_engine"], identity.resolved_engine),
                ("actual_engine", event["actual_engine"], identity.actual_engine),
            )
            for field, actual, expected in exact_fields:
                if field == "peer":
                    matches = peer_identity_equal(actual, expected)
                else:
                    matches = actual == expected
                if not matches:
                    raise HarnessProtocolError(f"health activation identity mismatch: {field}")
            if is_terminal_failure:
                self._terminal_identity = identity
        self._highest_generation = max(self._highest_generation, generation)
        return event

    def accept_shutdown_ack(
        self,
        payload: bytes,
        *,
        peer: PeerIdentity,
        expected_nonce: str,
    ) -> dict[str, Any]:
        if self.control_lost or self.peer is None or not peer_identity_equal(peer, self.peer):
            raise HarnessProtocolError("shutdown acknowledgement from inactive peer")
        if self._session_binding is None or self._stream_key is None:
            raise HarnessProtocolError("shutdown acknowledgement before session command")
        if self._shutdown_acknowledged:
            raise HarnessProtocolError("duplicate shutdown acknowledgement")
        acknowledgement = decode_shutdown_ack(
            payload,
            expected_session_ref=self._session_binding,
            expected_nonce=expected_nonce,
        )
        self._shutdown_acknowledged = True
        # The acknowledgement is the terminal credential boundary.  Retain
        # only the non-secret fact above; control revocation makes every later
        # command, event, or duplicate acknowledgement fail closed.
        self.revoke_control()
        return acknowledgement

    def lose_control(self) -> None:
        self.revoke_control()
        raise HarnessProtocolError("control connection lost")

    def revoke_control(self) -> None:
        """Clear all credential-bearing admission state on control loss."""

        self.control_lost = True
        self._stream_key = None
        self._session_binding = None

    def require_active(self) -> None:
        if self.control_lost or self.peer is None or self._session_binding is None or self._stream_key is None:
            raise HarnessProtocolError("control is not active")


_harness_state_accept_command = HarnessState.accept_command


def _accept_command_after_ack_guard(
    state: HarnessState,
    payload: bytes,
    *args: object,
    **kwargs: object,
) -> dict[str, Any]:
    if state._shutdown_acknowledged:
        raise HarnessProtocolError("session command after shutdown acknowledgement")
    return _harness_state_accept_command(state, payload, *args, **kwargs)


HarnessState.accept_command = _accept_command_after_ack_guard  # type: ignore[method-assign]


class UnixHarnessServer:
    """AF_UNIX server with mode-bound run directory and kernel peer admission."""

    def __init__(self, socket_path: str | os.PathLike[str]) -> None:
        self.socket_path = Path(socket_path)
        self.listener: socket.socket | None = None
        self._connections: set[socket.socket] = set()
        self._decoders: dict[int, FrameDecoder] = {}
        self._pending_events: dict[int, deque[bytes]] = {}

    def bind(self) -> None:
        directory = self.socket_path.parent
        if directory.exists():
            if not directory.is_dir():
                raise HarnessProtocolError("control path parent is not a directory")
        else:
            directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        os.chmod(directory, 0o700)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            listener.listen(1)
            self.listener = listener
        except Exception:
            listener.close()
            raise

    def accept_authenticated(
        self,
        state: HarnessState,
        *,
        peer_reader: PeerReader,
        timeout: float = 15.0,
    ) -> tuple[socket.socket, PeerIdentity]:
        """Read kernel identity before accepting any credential bytes."""

        if self.listener is None:
            raise HarnessProtocolError("server is not bound")
        self.listener.settimeout(timeout)
        try:
            connection, _ = self.listener.accept()
        except (TimeoutError, socket.timeout) as exc:
            raise HarnessProtocolError("peer timeout") from exc
        try:
            peer = peer_reader(connection)
            state.accept_peer(peer)
            connection.settimeout(timeout)
            self._connections.add(connection)
            self._decoders[id(connection)] = FrameDecoder()
            self._pending_events[id(connection)] = deque()
            return connection, peer
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def send_one_session(
        connection: socket.socket,
        state: HarnessState,
        peer: PeerIdentity,
        *,
        peer_revalidator: Callable[[], PeerIdentity],
        attested_identity: object,
        static_identity: StaticCodeIdentity,
        session_id: str,
        stream_key: str,
        gateway: str,
        timeout: float = 15.0,
    ) -> None:
        """Atomically recheck identity, admit, encode, and write one command.

        This is the sole credential-send transaction.  The caller may perform
        pre-attestation checks, but the final same-descriptor peer reread and
        strict dynamic/static identity comparison live here, immediately
        before command encoding and ``sendall``.  Once ``peer_revalidator``
        returns there are no user callbacks, awaits, sleeps, or filesystem
        operations before the credential-bearing frame crosses the socket.
        """

        if not callable(peer_revalidator):
            raise HarnessProtocolError("final authenticated peer revalidator is required")
        try:
            final_peer = peer_revalidator()
        except HarnessProtocolError:
            raise
        except Exception as exc:
            raise HarnessProtocolError("final authenticated peer reread failed") from exc
        if type(final_peer) is not PeerIdentity:
            raise HarnessProtocolError("final authenticated peer identity is invalid")
        if not peer_identity_equal(final_peer, peer):
            raise HarnessProtocolError("authenticated peer changed at command boundary")
        try:
            accepted_token = _audit_token_bytes(peer.audit_token)
            final_token = _audit_token_bytes(final_peer.audit_token)
        except HarnessProtocolError as exc:
            raise HarnessProtocolError("authenticated peer audit token is malformed") from exc
        if accepted_token != final_token:
            raise HarnessProtocolError("authenticated audit token changed at command boundary")
        require_exact_static_code_identity(static_identity, label="static identity")
        require_exact_static_code_identity(
            attested_identity,
            static_identity,
            label="dynamic attestor result",
        )

        # All final checks have passed.  Keep encoding/admission/send adjacent
        # so no external callback can run between the reread and wire bytes.
        wire = encode_session_command(
            session_id=session_id,
            stream_key=stream_key,
            gateway=gateway,
            launch_nonce=state.launch_nonce,
        )
        try:
            state.accept_command(wire[4:], peer=final_peer)
            connection.settimeout(timeout)
            connection.sendall(wire)
        except (TimeoutError, socket.timeout) as exc:
            # Admission is the point at which the state starts retaining the
            # command binding.  Revoke unconditionally: this also covers a
            # timeout/interrupt raised by the admission or socket setup
            # boundary, and keeps the cleanup invariant independent of which
            # sub-operation failed.
            state.revoke_control()
            raise HarnessProtocolError("command write timeout") from exc
        except BaseException:
            # Admission precedes the kernel write so event validation can bind
            # the exact command.  If that write (or timeout setup) fails, the
            # admitted credential/session must be retired before preserving
            # the original failure, including non-Exception control signals.
            state.revoke_control()
            raise

    @staticmethod
    def send_shutdown_request(
        connection: socket.socket,
        state: HarnessState,
        *,
        session_ref: str,
        nonce: str,
        timeout: float = 15.0,
    ) -> None:
        """Write one nonce-bound stop request without exposing credentials."""

        wire = encode_shutdown_request(session_ref=session_ref, nonce=nonce)
        try:
            connection.settimeout(timeout)
            connection.sendall(wire)
            connection.shutdown(socket.SHUT_WR)
        except (TimeoutError, socket.timeout) as exc:
            state.revoke_control()
            raise HarnessProtocolError("shutdown request write timeout") from exc
        except BaseException:
            state.revoke_control()
            raise

    def receive_event(
        self,
        connection: socket.socket,
        state: HarnessState,
        peer: PeerIdentity,
        *,
        timeout: float = 15.0,
        should_stop: Callable[[], bool] | None = None,
        shutdown_nonce_value: str | None = None,
    ) -> dict[str, Any]:
        """Read one event with fragmentation/coalescing and bounded timeout.

        A read deadline is a liveness polling interval, not proof that the
        authenticated peer disappeared.  The companion keeps this method
        alive across any number of quiet intervals and only fails closed for
        EOF, an OS error, or a decoder/schema/identity rejection.  A stop
        callback lets teardown interrupt the polling loop; its short poll
        slice also avoids relying on cross-thread ``socket.close`` to wake a
        blocking ``recv`` on Darwin.
        """

        if connection not in self._connections:
            raise HarnessProtocolError("unknown control connection")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise HarnessProtocolError("control read timeout must be positive")
        decoder = self._decoders[id(connection)]
        # A caller that can signal teardown must not be held by the full
        # production deadline: close() from another thread is not guaranteed
        # to interrupt recv() on macOS.  The requested deadline remains the
        # liveness interval when it is shorter than this bound.
        read_timeout = min(float(timeout), 0.25) if should_stop is not None else float(timeout)
        while True:
            if should_stop is not None and should_stop():
                raise HarnessProtocolError("control reader stopped")
            pending = self._pending_events[id(connection)]
            if pending:
                payload = pending.popleft()
                try:
                    if shutdown_nonce_value is not None:
                        try:
                            candidate = _decode_canonical_object(payload)
                        except HarnessProtocolError:
                            candidate = None
                        if isinstance(candidate, dict) and candidate.get("type") == "shutdown_ack":
                            # The acknowledgement closes the terminal
                            # request transaction.  It is admissible only if
                            # this is the sole complete post-request frame
                            # and the incremental decoder has no partial
                            # trailing bytes.  Earlier valid events may have
                            # been processed normally; anything after this
                            # exact ack revokes the connection instead of
                            # allowing a snapshot/PASS to survive.
                            if pending:
                                state.revoke_control()
                                raise HarnessProtocolError(
                                    "shutdown acknowledgement is not the sole terminal frame"
                                )
                            decoder.finish()
                            return state.accept_shutdown_ack(
                                payload,
                                peer=peer,
                                expected_nonce=shutdown_nonce_value,
                            )
                    return state.accept_event(payload, peer=peer)
                except Exception:
                    # A decoded frame that fails session, schema, generation,
                    # or activation-identity validation revokes the whole
                    # connection.  It must never leave an earlier activation
                    # eligible for a positive claim.
                    state.revoke_control()
                    raise
            connection.settimeout(read_timeout)
            try:
                chunk = connection.recv(8192)
            except (TimeoutError, socket.timeout) as exc:
                # An open, authenticated descriptor is healthy even when no
                # event arrives before this bounded read interval.  Recheck
                # teardown and poll again; EOF/errors below remain terminal.
                if should_stop is not None and should_stop():
                    raise HarnessProtocolError("control reader stopped") from exc
                continue
            except OSError as exc:
                state.revoke_control()
                raise HarnessProtocolError("control connection read failed") from exc
            if not chunk:
                state.revoke_control()
                raise HarnessProtocolError("control connection lost")
            try:
                payloads = decoder.feed(chunk)
            except HarnessProtocolError:
                state.revoke_control()
                raise
            # A stream read may contain multiple complete, valid frames.  They
            # are queued in wire order; coalescing is not duplicate input.
            pending.extend(payloads)

    def close_connection(self, connection: socket.socket) -> None:
        self._decoders.pop(id(connection), None)
        self._pending_events.pop(id(connection), None)
        self._connections.discard(connection)
        try:
            connection.close()
        except OSError:
            pass

    def close(self) -> None:
        for connection in tuple(self._connections):
            self.close_connection(connection)
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


def functional_permission(*, pcm_samples: Iterable[float] | None, explicit_denied: bool = False) -> str:
    if explicit_denied:
        return "denied"
    if pcm_samples is None:
        return "unknown"
    values = list(pcm_samples)
    if not values or any(not isinstance(value, (int, float)) or not _finite(value) for value in values):
        return "unknown"
    if not any(value != 0 for value in values):
        return "unknown"
    return "granted"


def _valid_device_identity(value: Any, *, actual_engine: str = PROCESS_TAP, kind: str = "running") -> bool:
    # v2 removes the free-form device_identity slot entirely.  The closed
    # actual_engine enum is the device class; this helper remains as a named
    # conjunct for mutation tests and rejects any attempted replacement.
    return value is None and actual_engine in {PROCESS_TAP, SCREEN_CAPTURE_KIT}


def functional_health(
    *,
    current_tuple: CaptureTuple | None,
    event_tuple: CaptureTuple,
    status: Mapping[str, Any] | None,
    actual_engine: str = PROCESS_TAP,
) -> bool:
    """Return whether one accepted health event proves functional capture.

    This is deliberately stricter than schema validity.  A matching event is
    only a functional grant while the source is in the live ``running`` state
    and every health field still satisfies the same safe conditions as
    ``SourceHealth.isHealthy`` (with ``sleep=awake`` required for live proof).
    """

    if current_tuple is None or event_tuple != current_tuple or not isinstance(status, Mapping):
        return False
    return all(
        (
            status.get("kind") == "running",
            status.get("permission") == "granted",
            status.get("route") == "healthy",
            status.get("interruption") == "clear",
            status.get("sleep") == "awake",
            status.get("overflowed") is False,
            _valid_device_identity(None, actual_engine=actual_engine, kind="running"),
        )
    )


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def transcript_claim(transcript: Iterable[Mapping[str, Any]]) -> bool:
    rows = list(transcript)
    return bool(rows) and all(
        row.get("source") in {"system_audio", "microphone"} and bool(row.get("text"))
        for row in rows
    )


def positive_process_tap_claim(proof: PositiveProcessTapProof) -> bool:
    """The only positive Process Tap claim predicate.

    This intentionally accepts one exact immutable type.  A mapping, truthy
    object, subclass, or field-by-field duck type is diagnostic material and
    can never authorize a canonical PASS.
    """

    if type(proof) is not PositiveProcessTapProof:
        return False
    activation = proof.activation
    functional_tuple = proof.functional_permission_tuple
    if type(activation) is not Activation or type(activation.tuple) is not CaptureTuple:
        return False
    if type(functional_tuple) is not CaptureTuple:
        return False
    capture = activation.tuple
    # CaptureTuple/Activation are immutable containers, but their annotated
    # fields are still caller-constructible.  Require exact scalar ownership
    # before any equality or enum check so truthy/subclass values cannot duck
    # through this terminal predicate.
    if any(
        type(field) is not str
        for field in (
            capture.kernel_peer,
            capture.launch_nonce,
            capture.attempt_id,
            activation.requested_engine,
            activation.resolved_engine,
            activation.actual_engine,
            functional_tuple.kernel_peer,
            functional_tuple.launch_nonce,
            functional_tuple.attempt_id,
        )
    ) or type(functional_tuple.generation) is not int:
        return False
    if (
        proof.artifact_valid is not True
        or proof.current_peer is not True
        or proof.transcript_valid is not True
        or type(proof.authenticated_peer_key) is not str
        or _PEER_FINGERPRINT.fullmatch(proof.authenticated_peer_key) is None
        or type(proof.launch_nonce) is not str
        or _PRODUCER_LAUNCH_NONCE.fullmatch(proof.launch_nonce) is None
        or capture.kernel_peer != proof.authenticated_peer_key
        or capture.launch_nonce != proof.launch_nonce
        or type(capture.generation) is not int
        or not 1 <= capture.generation <= (1 << 64) - 1
        or _PRODUCER_LAUNCH_NONCE.fullmatch(capture.launch_nonce) is None
        or _ATTEMPT_ID.fullmatch(capture.attempt_id) is None
        or _PRODUCER_LAUNCH_NONCE.fullmatch(functional_tuple.launch_nonce) is None
        or _ATTEMPT_ID.fullmatch(functional_tuple.attempt_id) is None
        or type(proof.functional_permission_state) is not str
        or proof.functional_permission_state != "granted"
        or functional_tuple != capture
        or not activation.is_process_tap()
    ):
        return False
    return True


def positive_process_tap_proof_payload(proof: PositiveProcessTapProof) -> dict[str, Any]:
    """Project every typed proof conjunct into deterministic JSON data."""

    if type(proof) is not PositiveProcessTapProof or not positive_process_tap_claim(proof):
        raise HarnessProtocolError("positive proof is not an exact qualified proof")
    activation = proof.activation
    capture = activation.tuple
    functional = proof.functional_permission_tuple

    def tuple_payload(value: CaptureTuple) -> dict[str, Any]:
        return {
            "kernel_peer": value.kernel_peer,
            "launch_nonce": value.launch_nonce,
            "attempt_id": value.attempt_id,
            "generation": value.generation,
        }

    return {
        "artifact_valid": proof.artifact_valid,
        "current_peer": proof.current_peer,
        "authenticated_peer_key": proof.authenticated_peer_key,
        "launch_nonce": proof.launch_nonce,
        "activation": {
            "tuple": tuple_payload(capture),
            "requested_engine": activation.requested_engine,
            "resolved_engine": activation.resolved_engine,
            "actual_engine": activation.actual_engine,
        },
        "functional_permission_state": proof.functional_permission_state,
        "functional_permission_tuple": tuple_payload(functional),
        "transcript_valid": proof.transcript_valid,
    }


def positive_process_tap_proof_digest(proof: PositiveProcessTapProof) -> str:
    """Return the exact binding retained beside canonical PASS facts."""

    return hashlib.sha256(canonical_json(positive_process_tap_proof_payload(proof))).hexdigest()


def restart_requires_fresh(previous: CaptureTuple, current: CaptureTuple) -> bool:
    """Require peer, nonce, and attempt to each be individually fresh."""

    if type(previous) is not CaptureTuple or type(current) is not CaptureTuple:
        return False
    if (
        type(previous.kernel_peer) is not str
        or _PEER_FINGERPRINT.fullmatch(previous.kernel_peer) is None
        or type(current.kernel_peer) is not str
        or _PEER_FINGERPRINT.fullmatch(current.kernel_peer) is None
        or type(previous.launch_nonce) is not str
        or _PRODUCER_LAUNCH_NONCE.fullmatch(previous.launch_nonce) is None
        or type(current.launch_nonce) is not str
        or _PRODUCER_LAUNCH_NONCE.fullmatch(current.launch_nonce) is None
        or type(previous.attempt_id) is not str
        or _ATTEMPT_ID.fullmatch(previous.attempt_id) is None
        or type(current.attempt_id) is not str
        or _ATTEMPT_ID.fullmatch(current.attempt_id) is None
        or type(previous.generation) is not int
        or not 1 <= previous.generation <= (1 << 64) - 1
        or type(current.generation) is not int
        or not 1 <= current.generation <= (1 << 64) - 1
    ):
        return False
    return (
        current.kernel_peer != previous.kernel_peer
        and current.launch_nonce != previous.launch_nonce
        and current.attempt_id != previous.attempt_id
    )


def secret_free(value: Any, sentinel: str | None) -> bool:
    return _typed_secret_free(value, sentinel, top_level=True)


def _contains_secret_field(value: Any, sentinel: str | None) -> bool:
    return _typed_contains_secret_material(value, sentinel, top_level=True)


def _canonical_dynamic(value: object, sentinel: str | None, *, field: str) -> None:
    """Validate diagnostic JSON independently of verifier-side ownership tags."""

    if is_dataclass(value) or type(value) in {tuple, set}:
        raise HarnessProtocolError(f"{field} contains an unprojected typed value")
    if type(value) is str:
        if credential_material(value, sentinel):
            raise HarnessProtocolError(f"credential-bearing diagnostic entered {field}")
        return
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not 0 <= value <= _UINT64_MAX:
            raise HarnessProtocolError(f"{field} contains an out-of-range integer")
        return
    if type(value) is float:
        if value != value or value in {float("inf"), float("-inf")}:
            raise HarnessProtocolError(f"{field} contains a non-finite number")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _canonical_dynamic(item, sentinel, field=f"{field}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or key.lower() in _SECRET_KEYS:
                raise HarnessProtocolError(f"{field} contains a secret or non-string key")
            if credential_material(key, sentinel):
                raise HarnessProtocolError(f"credential-bearing diagnostic entered {field}.key")
            _canonical_dynamic(item, sentinel, field=f"{field}.{key}")
        return
    raise HarnessProtocolError(f"{field} is not an exact JSON diagnostic shape")


def _canonical_artifact_facts(value: object, sentinel: str | None) -> None:
    if type(value) is not dict or set(value) != {
        "provenance_head", "provenance_tree", "dirty", "bundle_id", "team_id",
        "hardened_runtime", "entitlements", "strict_signature", "executable_digest",
        "sealed_executable_digest", "provenance_digest", "developer_id_authority",
        "audio_input_entitlement", "static_identity",
    }:
        raise HarnessProtocolError("artifact facts do not have an exact projection")
    for field in ("provenance_head", "provenance_tree"):
        if type(value[field]) is not str or _HEX40.fullmatch(value[field]) is None:
            raise HarnessProtocolError(f"artifact fact {field} is invalid")
        if sentinel and sentinel in value[field]:
            raise HarnessProtocolError(f"artifact fact {field} contains the sentinel")
    for field in ("executable_digest", "sealed_executable_digest", "provenance_digest"):
        if type(value[field]) is not str or _HEX64.fullmatch(value[field]) is None:
            raise HarnessProtocolError(f"artifact fact {field} is invalid")
        if sentinel and sentinel in value[field]:
            raise HarnessProtocolError(f"artifact fact {field} contains the sentinel")
    for field in ("bundle_id", "team_id"):
        if type(value[field]) is not str or not value[field]:
            raise HarnessProtocolError(f"artifact fact {field} is invalid")
        if sentinel and sentinel in value[field]:
            raise HarnessProtocolError(f"artifact fact {field} contains the sentinel")
    for field in ("dirty", "hardened_runtime", "strict_signature", "developer_id_authority", "audio_input_entitlement"):
        if type(value[field]) is not bool:
            raise HarnessProtocolError(f"artifact fact {field} is not an exact boolean")
    if type(value["entitlements"]) is not list or not all(type(item) is str for item in value["entitlements"]):
        raise HarnessProtocolError("artifact entitlements are not an exact list")
    for item in value["entitlements"]:
        if sentinel and sentinel in item:
            raise HarnessProtocolError("artifact entitlement contains the sentinel")
    identity = value["static_identity"]
    if type(identity) is not dict or set(identity) != {"unique_cdhash", "designated_requirement"}:
        raise HarnessProtocolError("artifact static identity is not an exact projection")
    unique_cdhash = identity["unique_cdhash"]
    designated_requirement = identity["designated_requirement"]
    if type(unique_cdhash) is not str or _CDHASH_HEX.fullmatch(unique_cdhash) is None:
        raise HarnessProtocolError("artifact static identity cdhash is invalid")
    # The designated requirement is a byte-string projection.  Keep its
    # length explicitly bounded before accepting the even-length lowercase
    # hexadecimal representation, so hostile giant strings cannot become
    # retained structural authority.
    if (
        type(designated_requirement) is not str
        or not 2 <= len(designated_requirement) <= 131072
        or len(designated_requirement) % 2
        or re.fullmatch(r"[0-9a-f]+", designated_requirement) is None
    ):
        raise HarnessProtocolError("artifact static identity requirement is invalid")
    for field in identity:
        if sentinel and sentinel in identity[field]:
            raise HarnessProtocolError("artifact static identity contains the sentinel")


def _canonical_phase_row(value: object, sentinel: str | None) -> None:
    if type(value) is not _TypedPhaseRow or set(value) != {"name", "status", "detail"}:
        raise HarnessProtocolError("phase row is not exact producer-owned data")
    name, status, detail = value["name"], value["status"], value["detail"]
    if type(name) is not str or name not in PHASE_ID_VALUES:
        raise HarnessProtocolError("phase row name is not a closed PhaseID")
    if type(status) is not str or status not in PHASE_STATUS_VALUES:
        raise HarnessProtocolError("phase row status is not a closed PhaseStatus")
    if type(detail) is not str:
        raise HarnessProtocolError("phase row detail is not an exact string")
    if sentinel and (sentinel in name or sentinel in status):
        raise HarnessProtocolError("phase row identity contains the sentinel")
    if status == "PASS":
        if detail != "producer template" or (sentinel and sentinel in detail):
            raise HarnessProtocolError("PASS phase row lacks its exact fixed detail")
    elif credential_material(detail, sentinel):
        raise HarnessProtocolError("phase diagnostic contains credential material")


def _validate_canonical_facts(facts: object, sentinel: str | None) -> None:
    """Canonical evidence's own exact root schema and ownership boundary."""

    if type(facts) is not dict or not all(type(key) is str for key in facts):
        raise HarnessProtocolError("canonical facts must be an exact object")
    if set(facts) - EVIDENCE_FACT_ALLOWLIST - _OPERATIONAL_FACT_KEYS:
        raise HarnessProtocolError("canonical facts contain unknown fields")
    if "transcript" in facts:
        raise HarnessProtocolError("raw transcript cannot enter canonical evidence")
    enum_values = {
        "arch": {"arm64", "x86_64", "i386", "arm64e"},
        "engine": {PROCESS_TAP, SCREEN_CAPTURE_KIT},
        "process_tap_evidence_result": {"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"},
        "tree_state": None,
    }
    fixed_strings = {"app_path", "generated_by", "machine", "signed_app", "timestamp", "tree_state", "voice"}
    count_fields = {
        "mic_bytes", "mic_frames", "mic_speech_frames", "segments_final",
        "segments_pre_stop", "segments_total", "transcript_candidate_hits", "transcript_interviewer_hits",
    }
    bool_fields = {
        "transcript_valid_typed", "transcript_restart_match", "transcription_complete",
        "process_tap_positive", "restart_drill",
    }
    closed_lists = {
        "transcript_speakers": {"Candidato", "Entrevistador"},
        "transcript_candidate_words": {"candidato", "experiencia", "vendas", "ingles"},
        "transcript_interviewer_words": {"entrevistador", "pergunta"},
    }
    for key, value in facts.items():
        if key in fixed_strings:
            if type(value) is not str or not value:
                raise HarnessProtocolError(f"canonical fact {key} is not an exact string")
            if sentinel and sentinel in value:
                raise HarnessProtocolError(f"canonical fact {key} contains the sentinel")
        elif key == "arch":
            if type(value) is not str or value not in enum_values[key]:
                raise HarnessProtocolError("canonical arch is not closed")
        elif key == "engine" or key == "process_tap_evidence_result":
            if type(value) is not str or value not in enum_values[key]:
                raise HarnessProtocolError(f"canonical enum {key} is invalid")
        elif key == "commit":
            if type(value) is not str or _HEX40.fullmatch(value) is None:
                raise HarnessProtocolError("canonical commit is not a 40-hex identity")
        elif key in {"expected_head", "expected_tree"}:
            if type(value) is not str or _HEX40.fullmatch(value) is None:
                raise HarnessProtocolError(f"canonical {key} is not a 40-hex identity")
        elif key == "expected_digest":
            if type(value) is not str or _HEX64.fullmatch(value) is None:
                raise HarnessProtocolError("canonical expected_digest is invalid")
        elif key == "proof_digest":
            if value is not None and (type(value) is not str or _HEX64.fullmatch(value) is None):
                raise HarnessProtocolError("canonical proof_digest is invalid")
        elif key == "artifact_facts":
            _canonical_artifact_facts(value, sentinel)
        elif key == "phase_rows":
            if type(value) is not list:
                raise HarnessProtocolError("canonical phase_rows is not an exact list")
            for row in value:
                _canonical_phase_row(row, sentinel)
        elif key in count_fields:
            if type(value) is not int or not 0 <= value <= _UINT64_MAX:
                raise HarnessProtocolError(f"canonical count {key} is invalid")
        elif key in bool_fields:
            if type(value) is not bool:
                raise HarnessProtocolError(f"canonical boolean {key} is invalid")
        elif key in closed_lists:
            if type(value) is not list or not all(type(item) is str and item in closed_lists[key] for item in value):
                raise HarnessProtocolError(f"canonical closed list {key} is invalid")
            if any(sentinel and sentinel in item for item in value):
                raise HarnessProtocolError(f"canonical closed list {key} contains the sentinel")
        else:
            _canonical_dynamic(value, sentinel, field=key)


def evidence_facts_projection(
    facts: Mapping[str, Any],
    *,
    include_operational: bool = False,
) -> dict[str, Any]:
    """Return the fixed set of fields allowed in retained evidence."""

    if not isinstance(facts, Mapping) or not all(isinstance(key, str) for key in facts):
        raise HarnessProtocolError("evidence facts must be an object")
    allowlist = EVIDENCE_FACT_ALLOWLIST | (_OPERATIONAL_FACT_KEYS if include_operational else frozenset())
    unknown = set(facts) - allowlist
    if unknown:
        raise HarnessProtocolError(f"evidence field allowlist violation: {sorted(unknown)}")
    if "transcript" in facts:
        raise HarnessProtocolError("raw transcript is diagnostic-only and cannot enter retained evidence")
    return {key: facts[key] for key in sorted(facts)}


_CANONICAL_EVIDENCE_MARKER = object()


class _CanonicalEvidence(dict[str, Any]):
    """In-memory canonical value with an immutable self-check snapshot."""

    __slots__ = ("_canonical_bytes", "_minted_marker")

    def __init__(
        self,
        payload: Mapping[str, Any],
        canonical_bytes: bytes,
        *,
        _authority: object | None = None,
    ) -> None:
        if _authority is not _CANONICAL_EVIDENCE_MARKER:
            raise HarnessProtocolError("canonical evidence requires the internal mint authority")
        if type(payload) is not dict or type(canonical_bytes) is not bytes:
            raise HarnessProtocolError("canonical evidence payload is not exact")
        super().__init__(payload)
        object.__setattr__(self, "_canonical_bytes", canonical_bytes)
        object.__setattr__(self, "_minted_marker", _CANONICAL_EVIDENCE_MARKER)

    def __setattr__(self, name: str, value: object) -> None:
        if name in self.__slots__ and hasattr(self, name):
            raise AttributeError("canonical evidence metadata is immutable")
        object.__setattr__(self, name, value)


def require_minted_canonical_evidence(value: object) -> bytes:
    """Validate the exact internal mint and return its immutable bytes.

    The marker is only an implementation invariant; every caller must also
    revalidate the complete canonical payload.  In particular, object type or
    identity alone is never treated as an in-process security authority.
    """

    if type(value) is not _CanonicalEvidence:
        raise HarnessProtocolError("evidence document must be minted by canonical_evidence")
    try:
        marker = object.__getattribute__(value, "_minted_marker")
        encoded = object.__getattribute__(value, "_canonical_bytes")
    except AttributeError as exc:
        raise HarnessProtocolError("canonical evidence mint metadata is missing") from exc
    if marker is not _CANONICAL_EVIDENCE_MARKER or type(encoded) is not bytes:
        raise HarnessProtocolError("canonical evidence mint metadata is invalid")
    try:
        if canonical_json(dict(value)) != encoded:
            raise HarnessProtocolError("evidence document was mutated after canonicalization")
    except (TypeError, ValueError) as exc:
        raise HarnessProtocolError("evidence document is not canonical") from exc
    return encoded


def _reject_unbound_stream_key_material(value: Any, *, field: str = "evidence") -> None:
    """Reject a key-shaped diagnostic when no active sentinel is available."""

    if type(value) is str:
        if _STREAM_KEY.fullmatch(value) is not None:
            raise HarnessProtocolError(f"key-shaped material entered {field}")
        # Also reject an exact key token embedded in a diagnostic sentence.
        if re.search(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", value):
            raise HarnessProtocolError(f"key-shaped material entered {field}")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise HarnessProtocolError(f"non-string key entered {field}")
            _reject_unbound_stream_key_material(key, field=f"{field}.key")
            _reject_unbound_stream_key_material(item, field=f"{field}.{key}")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _reject_unbound_stream_key_material(item, field=f"{field}[{index}]")
        return


def _canonical_phase_rows_are_positive(rows: object, *, restart_drill: object) -> bool:
    if type(restart_drill) is not bool:
        return False
    if type(rows) is not list:
        return False
    names: list[str] = []
    for raw_row in rows:
        row = _TypedPhaseRow(raw_row) if type(raw_row) is dict else raw_row
        try:
            _canonical_phase_row(row, None)
        except HarnessProtocolError:
            return False
        if row["status"] != "PASS":
            return False
        names.append(row["name"])
    conditional = {
        "Reinício do companion",
        "Fala pós-reinício transcrita",
        "Companion — cleanup após rejeição",
        "Companion — cleanup após falha terminal",
        "Companion — cleanup após falha",
    }
    base = PHASE_ID_VALUES - conditional
    restart = {"Reinício do companion", "Fala pós-reinício transcrita"}
    expected = base | restart if restart_drill else base
    return len(names) == len(expected) and set(names) == expected and len(set(names)) == len(names)


def _canonical_artifact_provenance_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json({
        "dirty": value["dirty"],
        "executable_sha256": value["executable_digest"],
        "head": value["provenance_head"],
        "tree": value["provenance_tree"],
    })).hexdigest()


def _validate_positive_canonical_facts(
    facts: Mapping[str, Any],
    proof: PositiveProcessTapProof,
) -> None:
    required = {
        "commit", "engine", "tree_state", "phase_rows", "transcription_complete",
        "transcript_valid_typed", "expected_head", "expected_tree", "expected_digest",
        "artifact_facts", "process_tap_positive", "process_tap_evidence_result", "proof_digest",
        "restart_drill",
    }
    if not required.issubset(facts):
        raise HarnessProtocolError("PASS evidence lacks exact operational facts")
    if (
        facts["engine"] != PROCESS_TAP
        or facts["process_tap_positive"] is not True
        or facts["process_tap_evidence_result"] != "PASS"
        or facts["transcription_complete"] is not True
        or facts["transcript_valid_typed"] is not True
        or facts["tree_state"] != "limpo"
        or facts["commit"] != facts["expected_head"]
        or not _canonical_phase_rows_are_positive(
            facts["phase_rows"], restart_drill=facts["restart_drill"]
        )
        or facts["proof_digest"] != positive_process_tap_proof_digest(proof)
        or not positive_process_tap_claim(proof)
    ):
        raise HarnessProtocolError("PASS evidence facts contradict the typed proof")
    artifact = facts["artifact_facts"]
    _canonical_artifact_facts(artifact, None)
    if (
        artifact["dirty"] is not False
        or artifact["bundle_id"] != "com.ellaexecutivesearch.tarscompanion"
        or artifact["team_id"] != "3FLG8W6B95"
        or artifact["hardened_runtime"] is not True
        or artifact["strict_signature"] is not True
        or artifact["developer_id_authority"] is not True
        or artifact["audio_input_entitlement"] is not True
        or artifact["entitlements"] != ["com.apple.security.device.audio-input"]
        or artifact["provenance_head"] != facts["expected_head"]
        or artifact["provenance_tree"] != facts["expected_tree"]
        or artifact["executable_digest"] != facts["expected_digest"]
        or artifact["sealed_executable_digest"] != facts["expected_digest"]
        or artifact["provenance_digest"] != _canonical_artifact_provenance_digest(artifact)
    ):
        raise HarnessProtocolError("PASS evidence artifact provenance is not exact")


def canonical_evidence(
    *,
    facts: Mapping[str, Any],
    result: str | None = None,
    sentinel: str | None = None,
    proof: PositiveProcessTapProof | None = None,
) -> dict[str, Any]:
    """Project fixed, secret-free evidence; PASS is never caller-selected."""

    if type(facts) is not dict:
        raise HarnessProtocolError("canonical evidence facts must be an exact object")
    _reject_unbound_stream_key_material(facts)
    _validate_canonical_facts(facts, sentinel)
    computed = False
    if proof is not None:
        if type(proof) is not PositiveProcessTapProof:
            raise HarnessProtocolError("positive proof is not the exact immutable type")
        computed = positive_process_tap_claim(proof)
    if result is None:
        result = "PASS" if computed else "INCONCLUSIVE"
    if type(result) is not str or result not in {"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"}:
        raise HarnessProtocolError("invalid evidence result")
    if result != "PASS" and set(facts) & _OPERATIONAL_FACT_KEYS:
        raise HarnessProtocolError("operational proof facts require a PASS projection")
    projected = evidence_facts_projection(facts, include_operational=result == "PASS")
    if _contains_secret_field(projected, sentinel):
        raise HarnessProtocolError("credential-bearing field entered evidence")
    if result == "PASS" and type(proof) is not PositiveProcessTapProof:
        raise HarnessProtocolError("PASS requires a typed positive proof")
    if result == "PASS" and not computed:
        raise HarnessProtocolError("positive proof predicate is false")
    if computed and result != "PASS":
        raise HarnessProtocolError("positive proof cannot be labeled non-PASS")
    if result == "PASS":
        _validate_positive_canonical_facts(projected, proof)
    safe = json.loads(canonical_json(projected).decode("utf-8"))
    output: dict[str, Any] = {"result": result, "facts": safe}
    if result == "PASS":
        output["claim"] = "process-tap-positive"
    encoded = canonical_json(output)
    return _CanonicalEvidence(
        json.loads(encoded.decode("utf-8")),
        encoded,
        _authority=_CANONICAL_EVIDENCE_MARKER,
    )


def markdown_projection(evidence: Mapping[str, Any]) -> str:
    require_minted_canonical_evidence(evidence)
    if set(evidence) - {"result", "facts", "claim"}:
        raise HarnessProtocolError("evidence top-level allowlist violation")
    result = evidence.get("result")
    if type(result) is not str or result not in {"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"}:
        raise HarnessProtocolError("evidence result is invalid")
    facts = evidence.get("facts")
    include_operational = result == "PASS"
    projected = (
        evidence_facts_projection(facts, include_operational=include_operational)
        if type(facts) is dict
        else None
    )
    if projected is None:
        raise HarnessProtocolError("evidence facts are invalid")
    _reject_unbound_stream_key_material(projected)
    # JSON serialization intentionally erases the in-memory producer marker
    # on phase rows.  The enclosing canonical evidence marker proves this
    # document was minted by us; restore only the exact row shape for schema
    # validation, never for granting authority.
    validation_facts = dict(projected)
    if type(validation_facts.get("phase_rows")) is list:
        validation_facts["phase_rows"] = [
            _TypedPhaseRow(row) if type(row) is dict else row
            for row in validation_facts["phase_rows"]
        ]
    _validate_canonical_facts(validation_facts, None)
    if result == "PASS":
        # Reconstructing the proof is intentionally impossible from a
        # document alone; canonical_evidence already bound its exact digest
        # and all positive facts before minting this value.  Recheck the
        # positive fact multiset so an in-memory mutation cannot reissue a
        # claim through Markdown.
        if evidence.get("claim") != "process-tap-positive":
            raise HarnessProtocolError("PASS evidence lacks its canonical claim")
        if not _canonical_phase_rows_are_positive(
            projected.get("phase_rows"), restart_drill=projected.get("restart_drill")
        ):
            raise HarnessProtocolError("PASS evidence phase rows are not exact")
        if projected.get("process_tap_positive") is not True or projected.get("process_tap_evidence_result") != "PASS":
            raise HarnessProtocolError("PASS evidence positive facts are invalid")
    try:
        canonical_json(projected)
    except HarnessProtocolError as exc:
        raise HarnessProtocolError("evidence facts are not canonical") from exc
    claim_present = "claim" in evidence
    if result == "PASS":
        if not claim_present or evidence.get("claim") != "process-tap-positive":
            raise HarnessProtocolError("PASS evidence lacks its canonical claim")
    elif claim_present:
        raise HarnessProtocolError("non-PASS evidence cannot carry a positive claim")
    lines = ["# Process Tap live-harness evidence", "", f"- Result: **{result}**"]
    if result == "PASS" and evidence.get("claim") == "process-tap-positive":
        lines.append("- Claim: **Process Tap positive**")
    lines.append("")
    for key, value in sorted(projected.items()):
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines) + "\n"


__all__ = [
    "Activation",
    "ActivationIdentity",
    "ArtifactFacts",
    "CFDictionaryKeyCallBacks",
    "CFDictionaryValueCallBacks",
    "CaptureTuple",
    "PositiveProcessTapProof",
    "DarwinRunningCodeAttestor",
    "DarwinSecurityBridge",
    "DarwinStaticCodeIdentityReader",
    "DarwinPeerIdentityReader",
    "DarwinAuditToken",
    "DarwinAuditTokenSignalSender",
    "EVIDENCE_FACT_ALLOWLIST",
    "FrameDecoder",
    "HarnessProtocolError",
    "HarnessState",
    "HEALTH_FIELDS",
    "HEALTH_FAILURE_CODES",
    "LiveHarnessFailureCode",
    "LaunchServicesAdapter",
    "LaunchResult",
    "LaunchSpec",
    "LaunchServicesProcess",
    "MacOSLaunchServicesAdapter",
    "MAX_PAYLOAD",
    "PERMISSION_DENIED_MESSAGE",
    "PeerIdentity",
    "PROCESS_TAP",
    "RunningCodeAttestor",
    "SCREEN_CAPTURE_KIT",
    "StaticCodeIdentity",
    "StaticCodeIdentityReader",
    "UnixHarnessServer",
    "attempt_binding",
    "artifact_provenance_digest",
    "artifact_provenance_payload",
    "canonical_evidence",
    "canonical_json",
    "credential_material",
    "decode_event",
    "decode_shutdown_ack",
    "decode_shutdown_request",
    "decode_session_command",
    "evidence_facts_projection",
    "encode_event",
    "encode_shutdown_ack",
    "encode_shutdown_request",
    "encode_session_command",
    "functional_health",
    "functional_permission",
    "frame",
    "make_launch_spec",
    "launch_binding",
    "markdown_projection",
    "observer_binding",
    "peer_fingerprint",
    "positive_process_tap_proof_digest",
    "positive_process_tap_proof_payload",
    "require_minted_canonical_evidence",
    "peer_matches",
    "positive_process_tap_claim",
    "peer_identity_equal",
    "restart_requires_fresh",
    "session_binding",
    "shutdown_binding",
    "shutdown_nonce",
    "source_binding",
    "redact_credential_material",
    "redact_fixed_material",
    "require_exact_static_code_identity",
    "secret_free",
    "shared_golden_session_command_payload",
    "transcript_claim",
    "validate_artifact",
    "validate_gateway_base",
    "validate_gateway_base_for_session",
    "validate_stream_key",
    "_TypedPhaseRow",
    "PHASE_ID_VALUES",
]

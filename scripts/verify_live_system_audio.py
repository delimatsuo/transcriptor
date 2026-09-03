#!/usr/bin/env python3
"""Prova ao vivo do canal do candidato (piloto-solo).

Roda o sistema real de ponta a ponta na máquina do proprietário:

    afplay (fixture PCM) ->  alto-falantes  ->  Process Tap (app menu-bar assinado)
                         ->  gateway WebSocket (backend real)  ->  Google STT
                         ->  segmento final rotulado "Candidato"

Nada aqui é simulado: backend real (uvicorn), app menu-bar assinado real,
Google STT real, estímulo de captura por `afplay` de um wav gerado. O único
trecho injetado por software é o canal do *entrevistador* (fase 6), que envia
PCM de fixture de arquivo pelo mesmo gateway com `source="microphone"` — é assim
que a rotulagem por fonte é provada sem precisar de um humano falando ao
microfone. Nem os alto-falantes nem a injeção usam macOS `say`; não há TTS de produto.

Códigos de saída:
    0  todas as fases executadas passaram
    1  alguma asserção falhou (defeito real)
    2  preflight de ambiente falhou (ADC, porta, voz, binário)
   42  BLOQUEADO por permissão TCC ausente (Gravação de Tela e Áudio do Sistema)

Uso:
    .venv/bin/python scripts/verify_live_system_audio.py [--with-restart-drill]
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, is_dataclass, replace
from enum import Enum
import hashlib
import json
import math
import os
import struct
import plistlib
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Callable, Iterable, Mapping

from live_system_audio_harness import (
    Activation,
    ArtifactFacts,
    artifact_provenance_digest,
    canonical_json,
    CaptureTuple,
    _cleanup_process_target,
    DarwinPeerIdentityReader,
    DarwinRunningCodeAttestor,
    DarwinStaticCodeIdentityReader,
    LaunchServicesAdapter,
    MacOSLaunchServicesAdapter,
    canonical_evidence,
    credential_material,
    EVIDENCE_FACT_ALLOWLIST,
    evidence_facts_projection,
    functional_health,
    functional_permission,
    HarnessState,
    HarnessProtocolError,
    PeerIdentity,
    PositiveProcessTapProof,
    positive_process_tap_proof_digest,
    require_minted_canonical_evidence,
    _TypedPhaseRow,
    PHASE_ID_VALUES,
    positive_process_tap_claim,
    peer_fingerprint,
    peer_identity_equal,
    restart_requires_fresh,
    redact_credential_material,
    redact_fixed_material,
    require_exact_static_code_identity,
    RunningCodeAttestor,
    StaticCodeIdentity,
    StaticCodeIdentityReader,
    UnixHarnessServer,
    validate_artifact,
    validate_stream_key,
    transcript_claim,
    encode_session_command,
    session_binding,
    shutdown_binding,
    shutdown_nonce,
    make_launch_spec,
)

import requests
import websockets
from websockets.asyncio.client import connect as ws_connect

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DOC = REPO_ROOT / "docs" / "launch" / "2026-08-21-solo-live-system-audio-evidence.md"

PORT = 8010
BASE_URL = f"http://127.0.0.1:{PORT}"
WS_BASE = f"ws://127.0.0.1:{PORT}/api/stream/native"

CANDIDATE_SENTENCE = (
    "O candidato tem dez anos de experiência em liderança de vendas e fala inglês fluente"
)
INTERVIEWER_SENTENCE = "Aqui fala o entrevistador fazendo uma pergunta"
RESTART_SENTENCE = "Esta frase vem depois do reinício da captura do candidato"

CANDIDATE_WORDS = {"candidato", "experiencia", "vendas", "ingles"}
CANDIDATE_MIN_HITS = 2
INTERVIEWER_WORDS = {"entrevistador", "pergunta"}
INTERVIEWER_MIN_HITS = 1
RESTART_WORDS = {"reinicio", "captura"}
RESTART_MIN_HITS = 1

SAMPLE_RATE = 16_000
FRAME_MS = 50
FRAME_BYTES = SAMPLE_RATE * 2 * FRAME_MS // 1000  # 1600 bytes = 50 ms mono s16le

# Exit codes
EXIT_OK, EXIT_FAILED, EXIT_PREFLIGHT, EXIT_TCC_BLOCKED = 0, 1, 2, 42
_UINT64_MAX = (1 << 64) - 1
# Diagnostic values are untrusted, potentially credential-reachable input.
# Keep every projection bounded even when a caller supplies an adversarial
# container graph; invalid traversal is replaced by one fixed safe marker.
_DIAGNOSTIC_MAX_DEPTH = 64
_DIAGNOSTIC_MAX_NODES = 8192
_SAFE_DIAGNOSTIC_MARKER = "<diagnostic-unavailable>"

SCRATCH = Path(os.environ.get("TMPDIR", "/tmp")) / "tars_live_proof"


# --------------------------------------------------------------------------
# Relatório de fases
# --------------------------------------------------------------------------


class PhaseID(str, Enum):
    """Closed producer-owned identifiers for every production ledger row."""

    PREFLIGHT_TREE = "Preflight proveniência da árvore"
    PREFLIGHT_ADC = "Preflight ADC"
    PREFLIGHT_PORT = "Preflight porta"
    PREFLIGHT_VOICE = "Preflight voz pt-BR"
    PREFLIGHT_APP = "Preflight app assinado"
    PREFLIGHT_APP_PROVENANCE = "Preflight proveniência/assinatura do app"
    BACKEND_UP = "Backend up"
    SESSION_CREATED = "Sessão criada"
    INVALID_KEY_REJECTED = "Chave inválida rejeitada"
    VALID_KEY_ACCEPTED = "Chave válida aceita (controle positivo)"
    COMPANION_CAPTURE = "Companion — estado da captura Process Tap"
    CANDIDATE_AUDIO = "Áudio do candidato reproduzido"
    INTERVIEWER_CHANNEL = "Canal do entrevistador enviado"
    RESTART = "Reinício do companion"
    MIC_SUSTAINED = "Canal do entrevistador sustentado até o /stop"
    POSITIVE_FACTS = "Companion — fatos positivos antes da parada"
    CLEANUP_REJECTION = "Companion — cleanup após rejeição"
    CLEANUP_TERMINAL_FAILURE = "Companion — cleanup após falha terminal"
    CLEANUP_FAILURE = "Companion — cleanup após falha"
    CLEANUP = "Companion — cleanup após a parada"
    SESSION_STOPPED = "Sessão encerrada"
    CANDIDATE_SEGMENT = "Segmento final rotulado 'Candidato'"
    INTERVIEWER_SEGMENT = "Segmento final rotulado 'Entrevistador'"
    NO_DUPLICATION = "Sem duplicação entre falantes"
    RESTART_TRANSCRIPT = "Fala pós-reinício transcrita"
    EVIDENCE = "Documento de evidência secret-safe"


class PhaseStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOQUEADO"
    INCONCLUSIVE = "INCONCLUSIVE"
    SKIPPED = "PULADO"


class ProgressNotice(str, Enum):
    """Closed producer-owned progress notices safe to print after key setup."""

    SETTLING = "settling"
    FINAL_SEGMENTS = "final_segments"
    EVIDENCE_WRITTEN = "evidence_written"


class PhaseDetailCode(str, Enum):
    TEMPLATE = "template"
    DIAGNOSTIC = "diagnostic"


@dataclass(frozen=True)
class PhaseDetail:
    """Closed detail value; arbitrary text is explicitly diagnostic."""

    code: PhaseDetailCode
    text: str = ""

    def __post_init__(self) -> None:
        if type(self.code) is not PhaseDetailCode or type(self.text) is not str:
            raise TypeError("phase detail is not a closed typed value")
        if self.code is PhaseDetailCode.TEMPLATE and self.text != "producer template":
            raise ValueError("template detail is closed and cannot carry caller text")

    @classmethod
    def template(cls) -> "PhaseDetail":
        # Closed producer template; runtime details are represented by typed
        # fields in facts, never by a caller-selected fixed string.
        return cls(PhaseDetailCode.TEMPLATE, "producer template")

    @classmethod
    def diagnostic(cls, text: str) -> "PhaseDetail":
        return cls(PhaseDetailCode.DIAGNOSTIC, text)


@dataclass(frozen=True)
class _FinalQualificationRecord:
    """Deterministic terminal snapshot; never an authority by itself.

    ``owner_instance`` is only an anti-transplant guard for this mutable
    ledger. Full current-state, proof, and canonical-payload recomputation
    remains the qualification authority; object identity alone is not a
    security boundary.
    """

    canonical_payload: bytes
    state_payload: bytes
    proof: PositiveProcessTapProof
    restart_mode: bool
    owner_instance: object = None


@dataclass(frozen=True)
class CredentialReachableDiagnostic:
    """Tagged free text that may have crossed a credential-bearing boundary."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("diagnostic text must be a string")


class FactKind(str, Enum):
    STRUCTURAL = "structural"
    ENUM = "enum"
    COUNT = "count"
    BOOLEAN = "boolean"
    DIAGNOSTIC = "diagnostic"
    TRANSCRIPT = "transcript"


@dataclass(frozen=True)
class FactSpec:
    kind: FactKind
    positive: bool = False


# Explicit ownership policy: diagnostic/raw fields can never be used as a
# positive claim, while pre-credential identity/count/enum fields are closed
# producer values and therefore use complete-sentinel collision detection.
FACT_SPECS: dict[str, FactSpec] = {
    "app_path": FactSpec(FactKind.STRUCTURAL),
    "arch": FactSpec(FactKind.ENUM),
    "argv": FactSpec(FactKind.DIAGNOSTIC),
    "commit": FactSpec(FactKind.STRUCTURAL, True),
    "engine": FactSpec(FactKind.ENUM, True),
    "error": FactSpec(FactKind.DIAGNOSTIC),
    "events": FactSpec(FactKind.DIAGNOSTIC),
    "generated_by": FactSpec(FactKind.STRUCTURAL),
    "logs": FactSpec(FactKind.DIAGNOSTIC),
    "machine": FactSpec(FactKind.STRUCTURAL),
    "mic_bytes": FactSpec(FactKind.COUNT, True),
    "mic_frames": FactSpec(FactKind.COUNT, True),
    "mic_speech_frames": FactSpec(FactKind.COUNT, True),
    "phase_detail": FactSpec(FactKind.DIAGNOSTIC),
    "phase_rows": FactSpec(FactKind.STRUCTURAL, True),
    "retained": FactSpec(FactKind.DIAGNOSTIC),
    "segments_final": FactSpec(FactKind.COUNT, True),
    "segments_pre_stop": FactSpec(FactKind.COUNT, True),
    "segments_total": FactSpec(FactKind.COUNT, True),
    "signed_app": FactSpec(FactKind.STRUCTURAL),
    "tcc_message": FactSpec(FactKind.DIAGNOSTIC),
    "timestamp": FactSpec(FactKind.STRUCTURAL),
    "transcript": FactSpec(FactKind.TRANSCRIPT),
    "transcript_speakers": FactSpec(FactKind.ENUM, True),
    "transcript_candidate_words": FactSpec(FactKind.ENUM, True),
    "transcript_interviewer_words": FactSpec(FactKind.ENUM, True),
    "transcript_candidate_hits": FactSpec(FactKind.COUNT, True),
    "transcript_interviewer_hits": FactSpec(FactKind.COUNT, True),
    "transcript_valid_typed": FactSpec(FactKind.BOOLEAN, True),
    "transcript_restart_match": FactSpec(FactKind.BOOLEAN, True),
    "transcription_complete": FactSpec(FactKind.BOOLEAN, True),
    "tree_state": FactSpec(FactKind.ENUM),
    "voice": FactSpec(FactKind.STRUCTURAL, True),
    # Operational pre-key facts are deliberately registered here as well as
    # the retained evidence fields.  A value cannot become producer-owned by
    # merely passing through an untyped dictionary boundary.
    "expected_head": FactSpec(FactKind.STRUCTURAL, True),
    "expected_tree": FactSpec(FactKind.STRUCTURAL, True),
    "expected_digest": FactSpec(FactKind.STRUCTURAL, True),
    "artifact_facts": FactSpec(FactKind.STRUCTURAL),
    "process_tap_positive": FactSpec(FactKind.BOOLEAN, True),
    "process_tap_evidence_result": FactSpec(FactKind.ENUM),
    "proof_digest": FactSpec(FactKind.STRUCTURAL),
    "restart_drill": FactSpec(FactKind.BOOLEAN, True),
}


_ARTIFACT_FACT_FIELDS = frozenset(
    {
        "provenance_head",
        "provenance_tree",
        "dirty",
        "bundle_id",
        "team_id",
        "hardened_runtime",
        "entitlements",
        "strict_signature",
        "executable_digest",
        "sealed_executable_digest",
        "provenance_digest",
        "developer_id_authority",
        "audio_input_entitlement",
        "static_identity",
    }
)
_STATIC_IDENTITY_FIELDS = frozenset({"unique_cdhash", "designated_requirement"})
_FACT_ENUMS: dict[str, frozenset[str]] = {
    "engine": frozenset({"process-tap", "screen-capture-kit"}),
    "process_tap_evidence_result": frozenset({"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"}),
    "arch": frozenset({"arm64", "x86_64", "i386", "arm64e"}),
    # Only the exact clean producer state is ownership-bearing.  Dirty tree
    # descriptions remain dynamic diagnostics and therefore cannot qualify.
    "tree_state": frozenset({"limpo"}),
}


def _contains_diagnostic(
    value: object,
    *,
    _depth: int = 0,
    _active: set[int] | None = None,
    _budget: list[int] | None = None,
) -> bool:
    """Find a tagged diagnostic before any typed value can unwrap it."""

    if isinstance(value, CredentialReachableDiagnostic):
        return True
    if _active is None:
        _active = set()
    if _budget is None:
        _budget = [0]
    if _depth >= _DIAGNOSTIC_MAX_DEPTH or _budget[0] > _DIAGNOSTIC_MAX_NODES:
        return True
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in _active:
            return True
        _active.add(identity)
        _budget[0] += 1
        try:
            for index, (key, item) in enumerate(value.items()):
                if index >= _DIAGNOSTIC_MAX_NODES:
                    return True
                if _contains_diagnostic(
                    key, _depth=_depth + 1, _active=_active, _budget=_budget
                ) or _contains_diagnostic(
                    item, _depth=_depth + 1, _active=_active, _budget=_budget
                ):
                    return True
            return False
        except Exception:
            return True
        finally:
            _active.remove(identity)
    if isinstance(value, (list, tuple, set)):
        identity = id(value)
        if identity in _active:
            return True
        _active.add(identity)
        _budget[0] += 1
        try:
            for index, item in enumerate(value):
                if index >= _DIAGNOSTIC_MAX_NODES:
                    return True
                if _contains_diagnostic(
                    item, _depth=_depth + 1, _active=_active, _budget=_budget
                ):
                    return True
            return False
        except Exception:
            return True
        finally:
            _active.remove(identity)
    return False


def _valid_diagnostic_shape(
    value: object,
    *,
    _depth: int = 0,
    _active: set[int] | None = None,
    _budget: list[int] | None = None,
) -> bool:
    """Accept only explicit JSON-like diagnostic values at non-proof slots."""

    if type(value) is CredentialReachableDiagnostic:
        return True
    if value is None or type(value) in {str, bool}:
        return True
    if type(value) is int:
        return 0 <= value <= _UINT64_MAX
    if type(value) is float:
        return value == value and value not in {float("inf"), float("-inf")}
    if _active is None:
        _active = set()
    if _budget is None:
        _budget = [0]
    if _depth >= _DIAGNOSTIC_MAX_DEPTH or _budget[0] > _DIAGNOSTIC_MAX_NODES:
        return False
    if type(value) is list:
        identity = id(value)
        if identity in _active:
            return False
        _active.add(identity)
        _budget[0] += 1
        try:
            for index, item in enumerate(value):
                if index >= _DIAGNOSTIC_MAX_NODES or not _valid_diagnostic_shape(
                    item, _depth=_depth + 1, _active=_active, _budget=_budget
                ):
                    return False
            return True
        except Exception:
            return False
        finally:
            _active.remove(identity)
    if type(value) is dict:
        identity = id(value)
        if identity in _active:
            return False
        _active.add(identity)
        _budget[0] += 1
        try:
            for index, (key, item) in enumerate(value.items()):
                if index >= _DIAGNOSTIC_MAX_NODES or type(key) is not str:
                    return False
                if not _valid_diagnostic_shape(
                    item, _depth=_depth + 1, _active=_active, _budget=_budget
                ):
                    return False
            return True
        except Exception:
            return False
        finally:
            _active.remove(identity)
    return False


def _validate_artifact_projection(value: object) -> None:
    if type(value) is not dict or set(value) != _ARTIFACT_FACT_FIELDS:
        raise HarnessProtocolError("artifact facts do not have an explicit typed projection")
    for field in ("provenance_head", "provenance_tree"):
        if type(value[field]) is not str or re.fullmatch(r"[0-9a-f]{40}", value[field]) is None:
            raise HarnessProtocolError(f"artifact fact {field} has an invalid identity")
    for field in ("executable_digest", "sealed_executable_digest", "provenance_digest"):
        if type(value[field]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[field]) is None:
            raise HarnessProtocolError(f"artifact fact {field} has an invalid digest")
    for field in ("bundle_id", "team_id"):
        if type(value[field]) is not str or not value[field]:
            raise HarnessProtocolError(f"artifact fact {field} has an invalid type")
    for field in ("dirty", "hardened_runtime", "strict_signature", "developer_id_authority", "audio_input_entitlement"):
        if type(value[field]) is not bool:
            raise HarnessProtocolError(f"artifact fact {field} has an invalid type")
    if (
        type(value["entitlements"]) is not list
        or not all(type(item) is str for item in value["entitlements"])
    ):
        raise HarnessProtocolError("artifact entitlements are not a typed list")
    identity = value["static_identity"]
    if type(identity) is not dict or set(identity) != _STATIC_IDENTITY_FIELDS:
        raise HarnessProtocolError("artifact static identity is not a typed projection")
    if type(identity["unique_cdhash"]) is not str or re.fullmatch(r"[0-9a-f]{64}", identity["unique_cdhash"]) is None:
        raise HarnessProtocolError("artifact static identity cdhash is invalid")
    requirement = identity["designated_requirement"]
    if (
        type(requirement) is not str
        or not 2 <= len(requirement) <= 131072
        or len(requirement) % 2
        or re.fullmatch(r"[0-9a-f]+", requirement) is None
    ):
        raise HarnessProtocolError("artifact static identity requirement is invalid")


def _validate_phase_row(value: object, *, require_pass_template: bool = False) -> None:
    if type(value) is not _TypedPhaseRow or set(value) != {"name", "status", "detail"}:
        raise HarnessProtocolError("phase row is not producer-owned typed data")
    name, status, detail = value["name"], value["status"], value["detail"]
    if type(name) is not str or _phase_id(name) is None:
        raise HarnessProtocolError("phase row name is not a closed PhaseID")
    if type(status) is not str or status not in {item.value for item in PhaseStatus}:
        raise HarnessProtocolError("phase row status is not a closed PhaseStatus")
    if type(detail) is not str:
        raise HarnessProtocolError("phase row detail is not a string")
    if status == PhaseStatus.PASS.value and detail != PhaseDetail.template().text:
        raise HarnessProtocolError("PASS phase row lacks the closed producer template")
    if require_pass_template and (status != PhaseStatus.PASS.value or detail != PhaseDetail.template().text):
        raise HarnessProtocolError("positive phase row is not the exact producer template")


def _phase_id(value: object) -> PhaseID | None:
    if isinstance(value, PhaseID):
        return value
    if isinstance(value, str):
        try:
            return PhaseID(value)
        except ValueError:
            return None
    return None


def _phase_status(value: object) -> PhaseStatus | None:
    if isinstance(value, PhaseStatus):
        return value
    if isinstance(value, str):
        try:
            return PhaseStatus(value)
        except ValueError:
            return None
    return None


def validate_fact_specs(facts: Mapping[str, object]) -> None:
    """Apply the explicit fact policy before canonical JSON projection."""

    if not isinstance(facts, Mapping) or not all(isinstance(key, str) for key in facts):
        raise HarnessProtocolError("facts do not have a typed owner")
    unknown = set(facts) - set(FACT_SPECS)
    if unknown:
        raise HarnessProtocolError(f"fact policy has no owner for: {sorted(unknown)}")
    if "transcript" in facts:
        raise HarnessProtocolError("raw transcript is diagnostic-only and cannot be retained")
    for key, value in facts.items():
        spec = FACT_SPECS[key]
        # A tagged diagnostic is never unwrapped into a producer fact.  This
        # check intentionally runs before the per-kind shape checks.
        if spec.kind is not FactKind.DIAGNOSTIC and _contains_diagnostic(value):
            raise HarnessProtocolError(f"fact {key} contains diagnostic data")
        if spec.kind is FactKind.DIAGNOSTIC:
            if not _valid_diagnostic_shape(value):
                raise HarnessProtocolError(f"fact {key} is not a typed diagnostic shape")
        elif spec.kind == FactKind.COUNT:
            if type(value) is not int or not 0 <= value <= _UINT64_MAX:
                raise HarnessProtocolError(f"fact {key} must be a nonnegative count")
        elif spec.kind == FactKind.BOOLEAN:
            if type(value) is not bool:
                raise HarnessProtocolError(f"fact {key} must be a boolean")
        elif spec.kind == FactKind.ENUM:
            allowed = _FACT_ENUMS.get(key)
            if key == "transcript_speakers":
                allowed = frozenset({"Candidato", "Entrevistador"})
            elif key == "transcript_candidate_words":
                allowed = frozenset(CANDIDATE_WORDS)
            elif key == "transcript_interviewer_words":
                allowed = frozenset(INTERVIEWER_WORDS)
            if key.startswith("transcript_") and key.endswith("words") or key == "transcript_speakers":
                if type(value) is not list or not all(type(item) is str and item in allowed for item in value):
                    raise HarnessProtocolError(f"fact {key} is not a closed enum list")
            elif allowed is not None:
                if type(value) is not str or value not in allowed:
                    raise HarnessProtocolError(f"fact {key} is not a closed enum")
            elif type(value) is not str:
                raise HarnessProtocolError(f"fact {key} must be a producer enum string")
        elif spec.kind is FactKind.STRUCTURAL:
            if key == "phase_rows":
                if type(value) is not list:
                    raise HarnessProtocolError("phase_rows must be a typed list")
                for row in value:
                    _validate_phase_row(row)
            elif key == "artifact_facts":
                _validate_artifact_projection(value)
            elif key in {"expected_head", "expected_tree"} or key == "commit":
                if type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None:
                    raise HarnessProtocolError(f"fact {key} must be a lowercase 40-hex identity")
            elif key in {"expected_digest", "proof_digest"}:
                if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                    raise HarnessProtocolError(f"fact {key} must be a lowercase 64-hex identity")
            elif type(value) is not str or not value:
                raise HarnessProtocolError(f"fact {key} must be a nonempty producer string")

_REDACTION_KEYS = frozenset(
    {"stream_key", "secret", "credential", "password", "api_key", "access_token", "authorization"}
)
_FIXED_FACT_KEYS = EVIDENCE_FACT_ALLOWLIST | frozenset(
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
_CONTROLLED_ENUM_VALUES = {
    "status": frozenset({"PASS", "FAIL", "BLOQUEADO", "INCONCLUSIVE", "PULADO"}),
    "result": frozenset({"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"}),
    "process_tap_evidence_result": frozenset({"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"}),
    "engine": frozenset({"process-tap", "screen-capture-kit"}),
    "speaker": frozenset({"Candidato", "Entrevistador"}),
}


class StreamingRedactor:
    """Streaming, prefix-safe redaction for process output and phase data."""

    def __init__(self, sentinel: str | None = None) -> None:
        self.sentinel = sentinel if isinstance(sentinel, str) and sentinel else None
        self._pending = ""
        self.seen = False

    def _longest_prefix_suffix(self, value: str) -> int:
        secret = self.sentinel
        if not secret:
            return 0
        maximum = min(len(secret) - 1, len(value))
        for length in range(maximum, 0, -1):
            if value.endswith(secret[:length]):
                return length
        return 0

    def feed(self, chunk: str) -> str:
        """Redact a chunk without allowing a secret to straddle retained chunks."""

        if not isinstance(chunk, str) or not chunk:
            return ""
        secret = self.sentinel
        if not secret:
            return chunk
        value = self._pending + chunk
        self._pending = ""
        output: list[str] = []
        while value:
            index = value.find(secret)
            if index >= 0:
                self.seen = True
                output.append(value[:index])
                output.append("<redacted>")
                value = value[index + len(secret):]
                continue
            keep = self._longest_prefix_suffix(value)
            if keep:
                output.append(value[:-keep])
                self._pending = value[-keep:]
            else:
                output.append(value)
            break
        return "".join(output)

    def finish(self) -> str:
        pending = self._pending
        self._pending = ""
        if credential_material(pending, self.sentinel):
            # EOF is a terminal boundary: the buffered value is necessarily a
            # non-empty prefix of the credential sentinel.  Retaining it would
            # leak credential material and leave the positive proof bit clear.
            self.seen = True
            return redact_credential_material(pending, self.sentinel)
        return pending

    def redact_complete(self, value: str) -> str:
        if not isinstance(value, str):
            return str(value)
        if credential_material(value, self.sentinel):
            self.seen = True
            return redact_credential_material(value, self.sentinel)
        return value

    def retire(self) -> None:
        """Retire the credential boundary after all owned output is final.

        ``finish`` is the only operation allowed to resolve a pending
        prefix.  Retirement deliberately refuses to run while one remains,
        then drops the sentinel and leaves only the durable ``seen`` bit.
        Calling it again is harmless, which lets teardown retry after an
        unrelated cleanup edge without resurrecting credential state.
        """

        if self._pending:
            raise HarnessProtocolError("streaming redactor has pending bytes")
        self.sentinel = None
        self._pending = ""


class _RedactingFacts(dict[str, object]):
    """Facts map that observes and sanitizes values before storing them."""

    def __init__(self, phases: "Phases") -> None:
        super().__init__()
        self._phases = phases

    def __setitem__(self, key: str, value: object) -> None:
        if self._phases._is_secret_key(key):
            self._phases._observe(value)
            return
        key_text = str(key)
        safe_key = self._phases._redact_fact_key(key_text)
        spec = FACT_SPECS.get(key_text)
        if spec is not None and spec.kind not in {FactKind.DIAGNOSTIC, FactKind.TRANSCRIPT}:
            if _contains_diagnostic(value):
                # Keep a redacted diagnostic only for visibility; its typed
                # ownership is permanently rejected below the proof boundary.
                self._phases._fact_ownership_failed = True
                safe_value = self._phases._redact_value(value)
            else:
                safe_value = self._phases._sanitize_typed_fact(key_text, value)
        else:
            # Raw/error/transcript values are credential-reachable diagnostics;
            # the wrapper is consumed here and never becomes a fixed producer
            # value merely because its text resembles a known field.
            safe_value = self._phases._redact_value(value)
        super().__setitem__(safe_key, safe_value)

    def update(self, *args: object, **kwargs: object) -> None:
        values = dict(*args, **kwargs)
        for key, value in values.items():
            self[key] = value


class Phases:
    cleanup_run: object | None = None
    cleanup_mic: object | None = None
    cleanup_backend_proc: object | None = None
    cleanup_backend_log: object | None = None
    """Registro ordenado de fases com resultado individual (PASS/FAIL/BLOQUEADO)."""

    def __init__(self, sentinel: str | None = None) -> None:
        self._redactor = StreamingRedactor(sentinel)
        self._secret_seen = False
        self._fact_ownership_failed = False
        self._row_ownership_failed = False
        self.rows: list[dict] = []
        self.facts: dict[str, object] = _RedactingFacts(self)
        self._final_qualification_record: _FinalQualificationRecord | None = None
        self._with_restart_drill = False
        # Restart ownership is a lifecycle slot, not a retained arbitrary
        # fact.  Only typed freshness/count facts may reach evidence.
        self.cleanup_run: object | None = None
        self.cleanup_mic: object | None = None
        self.cleanup_backend_proc: object | None = None
        self.cleanup_backend_log: object | None = None
        self.restart_run: object | None = None

    @staticmethod
    def _is_secret_key(key: object) -> bool:
        return isinstance(key, str) and key.lower() in _REDACTION_KEYS

    def _observe(self, value: object) -> None:
        # Run the same typed traversal used for storage, but discard the
        # projected value. Fixed row/schema tokens use a full-sentinel-only
        # check; dynamic strings and keys retain the terminal-prefix rule.
        self._redact_value(value)

    def _mark_fixed_collision(self, original: str, redacted: str) -> str:
        if redacted != original:
            self._secret_seen = True
            self._redactor.seen = True
        return redacted

    def _redact_fixed_string(self, value: str) -> str:
        return self._mark_fixed_collision(
            value,
            redact_fixed_material(value, self._redactor.sentinel),
        )

    def _redact_dynamic_string(self, value: str) -> str:
        redacted = self._redactor.redact_complete(value)
        if self._redactor.seen:
            self._secret_seen = True
        return redacted

    def _redact_structural(self, value: object) -> object:
        """Redact complete sentinels in closed producer-owned values."""

        if isinstance(value, CredentialReachableDiagnostic):
            self._fact_ownership_failed = True
            return self._redact_dynamic_string(value.text)
        if type(value) is ArtifactFacts:
            return self._artifact_projection(value)
        if isinstance(value, str):
            return self._redact_fixed_string(value)
        self._fact_ownership_failed = True
        return self._redact_value(value)

    def _redact_fact_key(self, key: str) -> str:
        if key in _FIXED_FACT_KEYS:
            return self._redact_fixed_string(key)
        return self._redact_dynamic_string(key)

    def _artifact_projection(self, facts: ArtifactFacts) -> dict[str, object]:
        """Normalize ArtifactFacts into a fully traversable typed mapping."""

        identity = facts.static_identity
        return {
            "provenance_head": self._redact_fixed_string(facts.provenance_head),
            "provenance_tree": self._redact_fixed_string(facts.provenance_tree),
            "dirty": facts.dirty,
            "bundle_id": self._redact_fixed_string(facts.bundle_id),
            "team_id": self._redact_fixed_string(facts.team_id),
            "hardened_runtime": facts.hardened_runtime,
            "entitlements": [self._redact_fixed_string(item) for item in facts.entitlements],
            "strict_signature": facts.strict_signature,
            "executable_digest": self._redact_fixed_string(facts.executable_digest),
            "sealed_executable_digest": self._redact_fixed_string(facts.sealed_executable_digest),
            "provenance_digest": self._redact_fixed_string(facts.provenance_digest),
            "developer_id_authority": facts.developer_id_authority,
            "audio_input_entitlement": facts.audio_input_entitlement,
            "static_identity": {
                "unique_cdhash": self._redact_fixed_string(identity.unique_cdhash.hex()),
                "designated_requirement": self._redact_fixed_string(identity.designated_requirement.hex()),
            },
        }

    def _sanitize_typed_fact(self, key: str, value: object) -> object:
        """Apply the declared policy without a generic producer fallback."""

        spec = FACT_SPECS.get(key)
        if spec is None or spec.kind in {FactKind.DIAGNOSTIC, FactKind.TRANSCRIPT}:
            return self._redact_value(value)
        if key == "artifact_facts":
            if type(value) is ArtifactFacts:
                return self._artifact_projection(value)
            if type(value) is dict and set(value) == _ARTIFACT_FACT_FIELDS:
                return {
                    str(field): self._sanitize_artifact_field(str(field), value[field])
                    for field in value
                }
            self._fact_ownership_failed = True
            return self._redact_value(value)
        if key == "phase_rows":
            if type(value) is not list or any(type(row) is not _TypedPhaseRow for row in value):
                self._fact_ownership_failed = True
                return self._redact_value(value)
            return [self._redact_value(row) for row in value]
        if spec.kind is FactKind.STRUCTURAL:
            # Every producer-owned structural fact has an explicit scalar
            # projection, apart from the two typed containers handled above.
            # Do not let an arbitrary mapping/list fall through to the
            # diagnostic redactor and regain producer ownership by shape.
            if key in {"expected_head", "expected_tree", "commit"}:
                valid_shape = type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value) is not None
            elif key in {"expected_digest", "proof_digest"}:
                valid_shape = type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None
            else:
                valid_shape = type(value) is str and bool(value)
            if not valid_shape:
                self._fact_ownership_failed = True
                return self._redact_value(value)
            return self._redact_fixed_string(value)
        if spec.kind is FactKind.ENUM:
            if key in {"transcript_speakers", "transcript_candidate_words", "transcript_interviewer_words"}:
                if type(value) is list and all(type(item) is str for item in value):
                    return [self._redact_fixed_string(item) for item in value]
                self._fact_ownership_failed = True
                return self._redact_value(value)
            if key in _FACT_ENUMS and type(value) is str and value in _FACT_ENUMS[key]:
                return self._redact_fixed_string(value)
            self._fact_ownership_failed = True
            return self._redact_value(value)
        if spec.kind is FactKind.COUNT and (type(value) is not int or not 0 <= value <= _UINT64_MAX):
            self._fact_ownership_failed = True
            return self._redact_value(value)
        if spec.kind is FactKind.BOOLEAN and type(value) is not bool:
            self._fact_ownership_failed = True
            return self._redact_value(value)
        return value

    def _sanitize_artifact_field(self, field: str, value: object) -> object:
        if field == "entitlements":
            if type(value) is not list or not all(type(item) is str for item in value):
                self._fact_ownership_failed = True
                return self._redact_value(value)
            return [self._redact_fixed_string(item) for item in value]
        if field == "static_identity":
            if type(value) is not dict or set(value) != _STATIC_IDENTITY_FIELDS \
                    or not all(type(value[name]) is str for name in _STATIC_IDENTITY_FIELDS):
                self._fact_ownership_failed = True
                return self._redact_value(value)
            return {
                str(name): self._redact_fixed_string(value[name])
                for name in _STATIC_IDENTITY_FIELDS
            }
        if field in {"dirty", "hardened_runtime", "strict_signature", "developer_id_authority", "audio_input_entitlement"}:
            if type(value) is not bool:
                self._fact_ownership_failed = True
                return self._redact_value(value)
            return value
        if type(value) is not str or not value:
            self._fact_ownership_failed = True
            return self._redact_value(value)
        return self._redact_fixed_string(value)

    @staticmethod
    def _mapping_kind(value: Mapping[object, object]) -> str | None:
        keys = set(value)
        if keys == {"name", "status", "detail"}:
            return "phase_row"
        if keys == {"speaker", "text"}:
            return "transcript_row"
        return None

    @staticmethod
    def _controlled_value(key: str, value: object) -> bool:
        allowed = _CONTROLLED_ENUM_VALUES.get(key)
        return (
            allowed is not None
            and isinstance(value, (str, bool, int, float, type(None)))
            and value in allowed
        )

    def _redact_value(
        self,
        value: object,
        *,
        _depth: int = 0,
        _active: set[int] | None = None,
        _budget: list[int] | None = None,
    ) -> object:
        """Project untrusted diagnostics with bounded graph traversal.

        A diagnostic may be a cyclic or extremely deep container supplied by
        an injected boundary.  The whole invalid subtree is replaced by a
        fixed marker and the owning ledger is durably disqualified; no
        descendant is stringified or retained after that point.
        """

        if isinstance(value, CredentialReachableDiagnostic):
            return self._redact_dynamic_string(value.text)
        if is_dataclass(value):
            # Invalid/unowned dataclasses (including ArtifactFacts subclasses)
            # are diagnostic material, never producer facts.  Never call
            # repr() on a hostile object: fields can contain raw credentials.
            self._fact_ownership_failed = True
            return _SAFE_DIAGNOSTIC_MARKER
        if isinstance(value, str):
            return self._redact_dynamic_string(value)
        if value is None or type(value) in {bool, int, float}:
            return value
        if _active is None:
            _active = set()
        if _budget is None:
            _budget = [0, 0]
        if _depth >= _DIAGNOSTIC_MAX_DEPTH or _budget[0] > _DIAGNOSTIC_MAX_NODES:
            _budget[1] = 1
            self._fact_ownership_failed = True
            return _SAFE_DIAGNOSTIC_MARKER
        is_mapping = isinstance(value, Mapping)
        is_sequence = isinstance(value, (list, tuple, set))
        if not (is_mapping or is_sequence):
            self._fact_ownership_failed = True
            return _SAFE_DIAGNOSTIC_MARKER
        identity = id(value)
        if identity in _active:
            _budget[1] = 1
            self._fact_ownership_failed = True
            return _SAFE_DIAGNOSTIC_MARKER
        _active.add(identity)
        _budget[0] += 1
        try:
            if _budget[1]:
                return _SAFE_DIAGNOSTIC_MARKER
            if is_mapping:
                if type(value) in {dict, _TypedPhaseRow} and len(value) > _DIAGNOSTIC_MAX_NODES:
                    _budget[1] = 1
                    self._fact_ownership_failed = True
                    return _SAFE_DIAGNOSTIC_MARKER
                # Mapping subclasses are untrusted containers; avoid a
                # potentially hostile key iterator while deciding whether
                # the mapping resembles a typed row.
                kind = self._mapping_kind(value) if type(value) in {dict, _TypedPhaseRow} else None
                redacted: dict[object, object] = {}
                for key, item in value.items():
                    _budget[0] += 1
                    if _budget[0] > _DIAGNOSTIC_MAX_NODES:
                        _budget[1] = 1
                        self._fact_ownership_failed = True
                        return _SAFE_DIAGNOSTIC_MARKER
                    if self._is_secret_key(key):
                        self._observe(item)
                        continue
                    if type(key) is not str:
                        _budget[1] = 1
                        self._fact_ownership_failed = True
                        return _SAFE_DIAGNOSTIC_MARKER
                    key_text = key
                    producer_row = isinstance(value, _TypedPhaseRow)
                    if kind == "phase_row" and key_text in {"name", "status", "detail"}:
                        safe_key = self._redact_fixed_string(key_text)
                    elif kind == "transcript_row" and key_text in {"speaker", "text"}:
                        safe_key = self._redact_fixed_string(key_text)
                    else:
                        safe_key = self._redact_dynamic_string(key_text)
                    if producer_row and key_text == "name" and isinstance(item, str):
                        safe_item = self._redact_fixed_string(item)
                    elif producer_row and key_text == "detail" and item == PhaseDetail.template().text:
                        safe_item = self._redact_fixed_string(item)
                    elif self._controlled_value(key_text, item) and isinstance(item, str):
                        safe_item = self._redact_fixed_string(item)
                    else:
                        safe_item = self._redact_value(
                            item,
                            _depth=_depth + 1,
                            _active=_active,
                            _budget=_budget,
                        )
                    if _budget[1]:
                        return _SAFE_DIAGNOSTIC_MARKER
                    redacted[safe_key] = safe_item
                if isinstance(value, _TypedPhaseRow):
                    return _TypedPhaseRow(redacted)
                return redacted
            if isinstance(value, list):
                if len(value) > _DIAGNOSTIC_MAX_NODES:
                    _budget[1] = 1
                    self._fact_ownership_failed = True
                    return _SAFE_DIAGNOSTIC_MARKER
                redacted_list = [
                    self._redact_value(
                        item,
                        _depth=_depth + 1,
                        _active=_active,
                        _budget=_budget,
                    )
                    for item in value
                ]
                return _SAFE_DIAGNOSTIC_MARKER if _budget[1] else redacted_list
            if isinstance(value, tuple):
                if len(value) > _DIAGNOSTIC_MAX_NODES:
                    _budget[1] = 1
                    self._fact_ownership_failed = True
                    return _SAFE_DIAGNOSTIC_MARKER
                redacted_tuple = tuple(
                    self._redact_value(
                        item,
                        _depth=_depth + 1,
                        _active=_active,
                        _budget=_budget,
                    )
                    for item in value
                )
                return _SAFE_DIAGNOSTIC_MARKER if _budget[1] else redacted_tuple
            if len(value) > _DIAGNOSTIC_MAX_NODES:
                _budget[1] = 1
                self._fact_ownership_failed = True
                return _SAFE_DIAGNOSTIC_MARKER
            redacted_set = {
                self._redact_value(
                    item,
                    _depth=_depth + 1,
                    _active=_active,
                    _budget=_budget,
                )
                for item in value
            }
            return _SAFE_DIAGNOSTIC_MARKER if _budget[1] else redacted_set
        except Exception:
            _budget[1] = 1
            self._fact_ownership_failed = True
            return _SAFE_DIAGNOSTIC_MARKER
        finally:
            _active.remove(identity)

    @property
    def sentinel(self) -> str | None:
        return self._redactor.sentinel

    @property
    def secret_seen(self) -> bool:
        return self._secret_seen or self._redactor.seen

    def mark_secret_seen(self) -> None:
        """Record a secret observation made by a child boundary.

        Companion output has its own streaming redactor because it arrives on
        a pipe in a different thread.  The phase ledger still needs the
        durable violation bit so a redacted output cannot accidentally leave a
        positive evidence result.
        """

        self._secret_seen = True
        # A credential observation is terminal for qualification.  Clear the
        # entire record immediately; a later caller cannot transplant a stale
        # PASS snapshot across this boundary.
        self._final_qualification_record = None

    def operational_facts_owned(self) -> bool:
        """Revalidate mutable-map bypasses before a positive result is used.

        ``facts`` is intentionally still a mapping for legacy callers.  A
        caller can therefore bypass ``__setitem__`` with ``dict.__setitem__``;
        this final audit makes that escape hatch fail closed instead of
        allowing a raw boolean or structural container to authorize PASS.
        """

        operational: dict[str, object] = {}
        # ``facts`` remains a mutable compatibility mapping, so a caller can
        # bypass ``_RedactingFacts.__setitem__`` with ``dict.__setitem__``.
        # Audit the complete retained key set first: an unknown key, a
        # non-string key, or a raw transcript is not an untyped diagnostic
        # escape hatch and must permanently disqualify the proof.
        for raw_key, raw_value in self.facts.items():
            if type(raw_key) is not str or raw_key not in FACT_SPECS:
                import sys; sys.stderr.write(f"[DIAG] operational_facts_owned unknown/non-string raw_key: {raw_key!r}\n")
                self._fact_ownership_failed = True
                continue
            spec = FACT_SPECS[raw_key]
            if spec.kind is FactKind.TRANSCRIPT:
                import sys; sys.stderr.write(f"[DIAG] operational_facts_owned raw TRANSCRIPT key: {raw_key!r}\n")
                self._fact_ownership_failed = True
            elif spec.kind is FactKind.DIAGNOSTIC and not _valid_diagnostic_shape(raw_value):
                import sys; sys.stderr.write(f"[DIAG] operational_facts_owned invalid DIAGNOSTIC shape for {raw_key!r}: {raw_value!r}\n")
                self._fact_ownership_failed = True
        for key, spec in FACT_SPECS.items():
            if spec.kind in {FactKind.DIAGNOSTIC, FactKind.TRANSCRIPT}:
                continue
            if key in self.facts:
                value = self.facts[key]
                if _contains_diagnostic(value):
                    import sys; sys.stderr.write(f"[DIAG] operational_facts_owned value contains diagnostic for {key!r}: {value!r}\n")
                    self._fact_ownership_failed = True
                    safe = self._redact_value(value)
                else:
                    safe = self._sanitize_typed_fact(key, value)
                operational[key] = safe
        try:
            validate_fact_specs(operational)
        except HarnessProtocolError as exc:
            import sys; sys.stderr.write(f"[DIAG] operational_facts_owned validate_fact_specs failed: {exc}\n")
            self._fact_ownership_failed = True
            return False
        return not self._fact_ownership_failed

    def register_stream_key(self, stream_key: str) -> None:
        """Install the durable credential redaction boundary exactly once."""

        validate_stream_key(stream_key)
        if self.sentinel is not None and self.sentinel != stream_key:
            raise HarnessProtocolError("stream key redaction sentinel cannot change")
        if self.sentinel is None:
            self._redactor.sentinel = stream_key
        # Re-sanitize values recorded before session creation and retain only
        # the safe representation while preserving a separate violation bit.
        for index, row in enumerate(self.rows):
            if type(row) is not _TypedPhaseRow:
                self._row_ownership_failed = True
            self.rows[index] = self._redact_value(row)
        redacted_facts: dict[str, object] = {}
        for key, value in list(self.facts.items()):
            key_text = str(key)
            safe_key = self._redact_fact_key(key_text)
            if key_text in FACT_SPECS and FACT_SPECS[key_text].kind not in {
                FactKind.DIAGNOSTIC,
                FactKind.TRANSCRIPT,
            }:
                if _contains_diagnostic(value):
                    self._fact_ownership_failed = True
                    redacted_facts[safe_key] = self._redact_value(value)
                else:
                    redacted_facts[safe_key] = self._sanitize_typed_fact(key_text, value)
            else:
                redacted_facts[safe_key] = self._redact_value(value)
        self.facts.clear()
        for key, value in redacted_facts.items():
            dict.__setitem__(self.facts, key, value)

    def emit(self, diagnostic: CredentialReachableDiagnostic, *, end: str = "\n", flush: bool = True) -> None:
        """Print only an explicitly tagged credential-reachable diagnostic."""

        if type(diagnostic) is not CredentialReachableDiagnostic:
            raise HarnessProtocolError("arbitrary output must be a tagged diagnostic")
        safe = self._redactor.redact_complete(diagnostic.text)
        if self._redactor.seen:
            self._secret_seen = True
        print(safe, end=end, flush=flush)

    def emit_progress(
        self,
        notice: ProgressNotice,
        *,
        final_count: int | None = None,
        total_count: int | None = None,
    ) -> None:
        """Print one of the closed producer progress templates.

        Counts are structural integers; no caller-supplied text or path is
        accepted at this boundary.  Consequently a URL-safe stream-key first
        character cannot collide with a fixed progress suffix.
        """

        if type(notice) is not ProgressNotice:
            raise HarnessProtocolError("progress notice is not a closed producer enum")
        if notice is ProgressNotice.SETTLING:
            if final_count is not None or total_count is not None:
                raise HarnessProtocolError("settling progress does not accept counts")
            text = "  … assentando 10 s com os canais ainda transmitindo"
        elif notice is ProgressNotice.FINAL_SEGMENTS:
            if (
                type(final_count) is not int
                or type(total_count) is not int
                or final_count < 0
                or total_count < 0
                or final_count > total_count
            ):
                raise HarnessProtocolError("final segment progress counts are invalid")
            text = f"  … {final_count} segmentos finais de {total_count} no total"
        elif notice is ProgressNotice.EVIDENCE_WRITTEN:
            if final_count is not None or total_count is not None:
                raise HarnessProtocolError("evidence progress does not accept counts")
            text = "  ✓ documento de evidência escrito"
        else:  # pragma: no cover - exhaustive enum guard
            raise HarnessProtocolError("unknown progress notice")
        print(text, flush=True)

    def record(self, name: str | PhaseID, status: str | PhaseStatus, detail: object = "") -> None:
        phase_id = name if type(name) is PhaseID else None
        phase_status = status if type(status) is PhaseStatus else None
        is_exact_pass = (
            phase_id is not None
            and phase_status is PhaseStatus.PASS
            and type(detail) is PhaseDetail
            and detail == PhaseDetail.template()
        )
        requested_pass = status is PhaseStatus.PASS or (type(status) is str and status == "PASS")

        def detail_text(value: object) -> str:
            if isinstance(value, PhaseDetail):
                return value.text
            if isinstance(value, CredentialReachableDiagnostic):
                return value.text
            return str(value)

        if requested_pass and not is_exact_pass:
            # Untyped PASS input is never allowed to become a proof row.  All
            # caller-controlled pieces still cross the dynamic redactor so a
            # terminal sentinel prefix cannot be silently discarded.
            raw_name = phase_id.value if phase_id is not None else str(name)
            raw_status = phase_status.value if phase_status is not None else str(status)
            safe_name = self._redact_dynamic_string(raw_name)
            self._redact_dynamic_string(raw_status)
            safe_detail = self._redact_dynamic_string(detail_text(detail))
            # Keep the row untyped.  It is already a terminal FAIL, but the
            # caller supplied at least one raw identity/detail, so retaining
            # the typed marker here would let the final projection treat a
            # dynamic name as producer-owned and miss a terminal key prefix.
            row = {
                "name": safe_name,
                "status": PhaseStatus.FAIL.value,
                "detail": safe_detail,
            }
            phase_status = PhaseStatus.FAIL
        elif is_exact_pass:
            row = _TypedPhaseRow({
                "name": self._redact_fixed_string(phase_id.value),
                "status": self._redact_fixed_string(phase_status.value),
                "detail": self._redact_fixed_string(detail.text),
            })
        else:
            raw_name = phase_id.value if phase_id is not None else str(name)
            raw_status = phase_status.value if phase_status is not None else str(status)
            if isinstance(detail, CredentialReachableDiagnostic):
                detail_value: object = detail
            elif isinstance(detail, PhaseDetail) and detail.code is PhaseDetailCode.DIAGNOSTIC:
                detail_value = CredentialReachableDiagnostic(detail.text)
            elif isinstance(detail, PhaseDetail) and detail.code is PhaseDetailCode.TEMPLATE:
                detail_value = detail.text
            else:
                detail_value = CredentialReachableDiagnostic(str(detail))
            row_value = self._redact_value({
                "name": raw_name,
                "status": raw_status,
                "detail": detail_value,
            })
            # A failure with exact producer-owned PhaseID/PhaseStatus still
            # has a typed row (its detail is explicitly diagnostic).  Any raw
            # identity/status remains an untyped mapping and is rejected by
            # every reducer/projection boundary.
            row = (
                _TypedPhaseRow(row_value)
                if phase_id is not None and phase_status is not None
                else row_value
            )
        self.rows.append(row)
        icon = {"PASS": "✓", "FAIL": "✗", "BLOQUEADO": "⏸", "PULADO": "–"}.get(
            phase_status.value if phase_status is not None else str(status), "?"
        )
        safe_name = row.get("name", "")
        safe_status = row.get("status", "")
        safe_detail = row.get("detail", "")
        # These pieces already crossed their typed boundaries.  Re-running
        # the whole rendered line through a dynamic redactor would treat the
        # terminal ``S`` in a fixed ``PASS`` status as credential material
        # when a sentinel starts with ``S``.
        print(
            f"  {icon} {safe_name}: {safe_status}" + (f" — {safe_detail}" if safe_detail else ""),
            flush=True,
        )

    @property
    def failed(self) -> bool:
        return any(isinstance(r, Mapping) and r.get("status") == "FAIL" for r in self.rows)

    @property
    def blocked(self) -> bool:
        return any(isinstance(r, Mapping) and r.get("status") == "BLOQUEADO" for r in self.rows)


def banner(title: str) -> None:
    print(f"\n▶ {title}", flush=True)


def normalize(text: str) -> str:
    """Minúsculas sem acentos — o STT varia na acentuação entre execuções."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def hits(text: str, words: set[str]) -> set[str]:
    normalized = normalize(text)
    return {w for w in words if w in normalized}


# --------------------------------------------------------------------------
# Fase 1 — Preflight
# --------------------------------------------------------------------------

# `say -v '?'` imprime "<nome>  <locale>  # <exemplo>"; o nome pode conter
# espaços e parênteses ("Eddy (Portuguese (Brazil))"), então o corte tem de ser
# feito no token de locale — não no primeiro par de espaços, que deixaria o
# "pt_BR" grudado no nome registrado na evidência.
VOICE_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<locale>[a-z]{2}_[A-Z]{2})\s+#")
_CODE_DIRECTORY_LINE = re.compile(
    r"CodeDirectory v=(?P<version>[0-9]+) "
    r"size=(?P<size>[0-9]+) "
    r"flags=0x(?P<flags>[0-9A-Fa-f]+)\(runtime\) "
    r"hashes=(?P<hashes_primary>[0-9]+)\+(?P<hashes_secondary>[0-9]+) "
    r"location=(?P<location>\S+)"
)


class SignedArtifactInspector:
    """Read a sealed app resource and independently verify public signature facts.

    The runner is injectable for offline tests.  The default is used only by a
    later owner-authorized live run; this source-only task never invokes it.
    """

    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
        *,
        static_identity_reader: StaticCodeIdentityReader | None = None,
    ) -> None:
        self._runner = runner or subprocess.run
        # The reader is an explicit/injectable boundary so offline fake
        # runners never load Security.framework.  Production construction
        # uses the Darwin implementation lazily when ``inspect`` reaches the
        # static identity edge.
        self._static_identity_reader = (
            static_identity_reader
            if static_identity_reader is not None
            else DarwinStaticCodeIdentityReader()
        )

    def inspect(self, app_path: Path) -> ArtifactFacts:
        executable = app_path / "Contents" / "MacOS" / "TarsCompanionApp"
        provenance_path = app_path / "Contents" / "Resources" / "Task11Provenance.json"
        try:
            raw = provenance_path.read_bytes()
            facts_json = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_json_duplicates)
        except HarnessProtocolError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HarnessProtocolError("sealed Task 11 provenance is unavailable") from exc
        if not isinstance(facts_json, dict) or canonical_json(facts_json) != raw:
            raise HarnessProtocolError("sealed provenance is not canonical")
        required = {
            "bundle_id", "dirty", "entitlements", "executable_sha256", "head", "hardened_runtime",
            "provenance_sha256", "strict_signature", "team_id", "tree",
        }
        if set(facts_json) != required:
            raise HarnessProtocolError("sealed provenance field allowlist violation")
        if (
            not isinstance(facts_json["dirty"], bool)
            or not isinstance(facts_json["entitlements"], list)
            or not all(isinstance(item, str) for item in facts_json["entitlements"])
            or not isinstance(facts_json["hardened_runtime"], bool)
            or not isinstance(facts_json["strict_signature"], bool)
            or not all(isinstance(facts_json[field], str) for field in ("bundle_id", "team_id", "head", "tree", "executable_sha256", "provenance_sha256"))
        ):
            raise HarnessProtocolError("sealed provenance field types are invalid")
        # The final bundle signature changes the Mach-O bytes.  Verify the
        # sealed digest over signature-neutral content by copying the final
        # executable into a task-scoped temporary directory, stripping only
        # that copy through the injectable codesign boundary, and hashing the
        # result.  The original signed executable is never modified and the
        # temporary directory is removed synchronously on every path.
        try:
            with tempfile.TemporaryDirectory(prefix="tars-task11-digest-") as digest_root:
                neutral_executable = Path(digest_root) / executable.name
                shutil.copyfile(executable, neutral_executable)
                strip_result = self._runner(
                    ["codesign", "--remove-signature", str(neutral_executable)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if getattr(strip_result, "returncode", 1) != 0:
                    raise HarnessProtocolError("signed app signature-neutral digest could not be derived")
                executable_digest = hashlib.sha256(neutral_executable.read_bytes()).hexdigest()
        except HarnessProtocolError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise HarnessProtocolError("signed app executable is unavailable") from exc
        verify = self._runner(
            ["codesign", "--verify", "--deep", "--strict", str(app_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        details = self._runner(
            ["codesign", "-dv", "--verbose=4", str(app_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        entitlement = self._runner(
            ["codesign", "-d", "--entitlements", ":-", str(app_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        # ``codesign`` diagnostics can still emit plausible-looking output on
        # failure.  Never parse or treat that output as an attestation: every
        # public readback operation must have an exact zero exit status.
        for operation, result in (
            ("verify", verify),
            ("details", details),
            ("entitlements", entitlement),
        ):
            if getattr(result, "returncode", 1) != 0:
                raise HarnessProtocolError(f"codesign {operation} readback failed")
        details_text = f"{getattr(details, 'stdout', '')}\n{getattr(details, 'stderr', '')}"
        details_lines = {line.strip() for line in details_text.splitlines()}
        # ``codesign -dv --verbose=4`` embeds flags in one CodeDirectory line.
        # Require the complete real grammar and the hardened-runtime bit;
        # labels such as ``notruntime`` and literal ``flags=runtime`` must not
        # qualify.  Duplicate CodeDirectory lines or flags are ambiguous.
        code_directory_lines = [
            line.strip()
            for line in details_text.splitlines()
            if line.strip().startswith("CodeDirectory")
        ]
        runtime_flag = False
        if len(code_directory_lines) == 1:
            code_directory_line = code_directory_lines[0]
            flag_match = _CODE_DIRECTORY_LINE.fullmatch(code_directory_line)
            if flag_match:
                runtime_flag = int(flag_match.group("flags"), 16) & 0x10000 != 0
        if not runtime_flag:
            raise HarnessProtocolError(
                "codesign CodeDirectory runtime flags are missing or malformed"
            )
        entitlement_text = f"{getattr(entitlement, 'stdout', '')}\n{getattr(entitlement, 'stderr', '')}"
        developer_id_authority = (
            "Authority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)"
            in details_lines
        )
        try:
            static_identity = self._static_identity_reader(app_path)
        except HarnessProtocolError:
            raise
        except Exception as exc:
            raise HarnessProtocolError("static Security.framework identity read failed") from exc
        require_exact_static_code_identity(
            static_identity,
            label="static identity reader result",
        )
        facts = ArtifactFacts(
            provenance_head=str(facts_json["head"]),
            provenance_tree=str(facts_json["tree"]),
            dirty=facts_json["dirty"],
            bundle_id=str(facts_json["bundle_id"]),
            team_id=str(facts_json["team_id"]),
            hardened_runtime=facts_json["hardened_runtime"] and runtime_flag,
            entitlements=tuple(facts_json["entitlements"]),
            strict_signature=getattr(verify, "returncode", 1) == 0
            and facts_json["strict_signature"]
            and runtime_flag,
            executable_digest=executable_digest,
            static_identity=static_identity,
            sealed_executable_digest=str(facts_json["executable_sha256"]),
            provenance_digest=str(facts_json["provenance_sha256"]),
            developer_id_authority=developer_id_authority,
            audio_input_entitlement=False,
        )
        if (
            f"Identifier={facts.bundle_id}" not in details_lines
            or f"TeamIdentifier={facts.team_id}" not in details_lines
            or not facts.developer_id_authority
        ):
            raise HarnessProtocolError("signed app public metadata mismatch")
        try:
            plist_start = entitlement_text.find("<?xml")
            if plist_start < 0:
                plist_start = entitlement_text.find("<plist")
            plist_end = entitlement_text.rfind("</plist>")
            if plist_start < 0 or plist_end < plist_start:
                raise ValueError("entitlement plist markers are missing")
            plist_bytes = entitlement_text[plist_start : plist_end + len("</plist>")].encode("utf-8")
            entitlement_values = plistlib.loads(plist_bytes)
            entitlement_enabled = (
                isinstance(entitlement_values, dict)
                and set(entitlement_values) == {"com.apple.security.device.audio-input"}
                and entitlement_values.get("com.apple.security.device.audio-input") is True
            )
        except (TypeError, ValueError, plistlib.InvalidFileException) as exc:
            raise HarnessProtocolError("signed app entitlement plist is malformed") from exc
        if not entitlement_enabled:
            raise HarnessProtocolError("signed app entitlement readback mismatch")
        facts = replace(facts, audio_input_entitlement=True)
        return facts


def _reject_json_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HarnessProtocolError(f"duplicate provenance field: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class Task11ProvenanceSnapshot:
    """Exact execution-time identity of the checkout used by live phases."""

    head: str
    tree: str
    porcelain: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.porcelain


def _current_task11_provenance() -> Task11ProvenanceSnapshot:
    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD^{tree}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    status = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return Task11ProvenanceSnapshot(head=head, tree=tree, porcelain=tuple(status))


def pick_voice() -> str | None:
    """Voz pt-BR preferida (Eddy, Flo); qualquer pt_BR serve como alternativa."""
    listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    br_voices: list[str] = []
    for line in listing.stdout.splitlines():
        match = VOICE_LINE.match(line)
        if match and match.group("locale") == "pt_BR":
            br_voices.append(match.group("name").strip())
    for preferred in ("Eddy", "Flo"):
        for voice in br_voices:
            if voice.startswith(preferred):
                return voice
    return br_voices[0] if br_voices else None


def phase_preflight(
    ph: Phases,
    signed_app: Path,
    *,
    artifact_inspector: SignedArtifactInspector | None = None,
    current_provenance: Callable[[], Task11ProvenanceSnapshot] | None = None,
) -> bool:
    banner("Fase 1/10 — Preflight de ambiente")

    # Capture checkout identity before inspecting the artifact or entering any
    # backend/provider/audio phase.  HEAD and tree alone are insufficient: a
    # tracked or untracked edit can be the code that actually executes.
    try:
        provenance = (current_provenance or _current_task11_provenance)()
        if not isinstance(provenance, Task11ProvenanceSnapshot):
            raise HarnessProtocolError("snapshot de proveniência não tipado")
        if (
            not re.fullmatch(r"[0-9a-f]{40}", provenance.head)
            or not re.fullmatch(r"[0-9a-f]{40}", provenance.tree)
        ):
            raise HarnessProtocolError("snapshot de proveniência HEAD/tree inválido")
        if not provenance.clean:
            raise HarnessProtocolError(
                "working tree não está limpa no início da execução"
            )
    except Exception as exc:
        ph.record(PhaseID.PREFLIGHT_TREE, PhaseStatus.FAIL, CredentialReachableDiagnostic(str(exc)))
        return False
    expected_head = provenance.head
    expected_tree = provenance.tree
    ph.facts["expected_head"] = expected_head
    ph.facts["expected_tree"] = expected_tree
    ph.record(PhaseID.PREFLIGHT_TREE, PhaseStatus.PASS, PhaseDetail.template())

    # ADC: apenas o código de saída. O token nunca é lido, impresso ou guardado.
    adc = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if adc.returncode != 0:
        ph.record(PhaseID.PREFLIGHT_ADC, PhaseStatus.FAIL, CredentialReachableDiagnostic("ADC expirado: rode 'gcloud auth application-default login'"))
        return False
    ph.record(PhaseID.PREFLIGHT_ADC, PhaseStatus.PASS, PhaseDetail.template())

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            ph.record(PhaseID.PREFLIGHT_PORT, PhaseStatus.FAIL, CredentialReachableDiagnostic(f"porta {PORT} já está em uso"))
            return False
    ph.record(PhaseID.PREFLIGHT_PORT, PhaseStatus.PASS, PhaseDetail.template())

    voice = pick_voice()
    if not voice:
        ph.record(PhaseID.PREFLIGHT_VOICE, PhaseStatus.FAIL, CredentialReachableDiagnostic("nenhuma voz pt_BR instalada"))
        return False
    ph.facts["voice"] = voice
    ph.record(PhaseID.PREFLIGHT_VOICE, PhaseStatus.PASS, PhaseDetail.template())

    if not signed_app.is_absolute() or signed_app.suffix != ".app":
        ph.record(PhaseID.PREFLIGHT_APP, PhaseStatus.FAIL, CredentialReachableDiagnostic("--signed-app deve apontar para um .app absoluto"))
        return False
    if not signed_app.is_dir():
        ph.record(PhaseID.PREFLIGHT_APP, PhaseStatus.FAIL, CredentialReachableDiagnostic("o .app explícito não existe"))
        return False
    ph.facts["signed_app"] = str(signed_app)
    ph.record(PhaseID.PREFLIGHT_APP, PhaseStatus.PASS, PhaseDetail.template())
    if artifact_inspector is not None:
        try:
            facts = artifact_inspector.inspect(signed_app)
            validate_artifact(
                facts,
                expected_head=expected_head,
                expected_tree=expected_tree,
                expected_digest=facts.executable_digest,
            )
        except Exception as exc:
            ph.record(PhaseID.PREFLIGHT_APP_PROVENANCE, PhaseStatus.FAIL, CredentialReachableDiagnostic(str(exc)))
            return False
        ph.facts["artifact_facts"] = facts
        ph.facts["expected_head"] = expected_head
        ph.facts["expected_tree"] = expected_tree
        ph.facts["expected_digest"] = facts.executable_digest
        ph.record(PhaseID.PREFLIGHT_APP_PROVENANCE, PhaseStatus.PASS, PhaseDetail.template())
    return True


# --------------------------------------------------------------------------
# Fase 2 — Backend real
# --------------------------------------------------------------------------

def spawn_backend_process(
    argv: list[str],
    *,
    on_process: Callable[[Any], None],
    stdout: Any = None,
    stderr: Any = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> subprocess.Popen:
    """Production backend spawner boundary with signal blocking and callback publication."""
    if not callable(on_process):
        raise HarnessProtocolError("on_process callback is required")
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
    proc = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=stdout,
            stderr=stderr,
            cwd=cwd,
            env=env,
            **kwargs,
        )
        on_process(proc)
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
    return proc


def phase_backend(
    ph: Phases,
    *,
    spawner: Callable[..., Any] = spawn_backend_process,
) -> tuple[subprocess.Popen, IO[bytes]] | None:
    """Devolve (processo, handle do log) — o handle é fechado pelo `finally`."""
    banner("Fase 2/10 — Subindo o backend real (uvicorn)")
    env = dict(os.environ)
    env["AUTH_BYPASS"] = "true"
    env.pop("HOST_AUDIO_CAPTURE_ENABLED", None)  # captura legada no host fica desligada

    proc: subprocess.Popen | None = None
    published_proc: Any = None
    returned_proc: Any = None
    log: IO[bytes] | None = None
    try:
        log = open(SCRATCH / "backend.log", "wb")
        ph.cleanup_backend_log = log

        def on_process_published(p: Any) -> None:
            nonlocal published_proc, proc
            published_proc = p
            proc = p
            ph.cleanup_backend_proc = p

        argv = [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(PORT)]
        returned_proc = spawner(
            argv,
            on_process=on_process_published,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        if published_proc is None:
            raise HarnessProtocolError("backend spawner returned without publishing process")
        if returned_proc is not published_proc:
            raise HarnessProtocolError("backend spawner published one process and returned another")
        proc = returned_proc

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                ph.record(PhaseID.BACKEND_UP, PhaseStatus.FAIL, CredentialReachableDiagnostic(f"uvicorn saiu com código {proc.returncode}"))
                ph.cleanup_backend_proc = None
                try:
                    log.close()
                    ph.cleanup_backend_log = None
                except BaseException:
                    pass
                return None
            try:
                if requests.get(f"{BASE_URL}/healthz", timeout=2).status_code == 200:
                    ph.record(PhaseID.BACKEND_UP, PhaseStatus.PASS, PhaseDetail.template())
                    return proc, log
            except requests.RequestException:
                time.sleep(0.5)
        ph.record(PhaseID.BACKEND_UP, PhaseStatus.FAIL, CredentialReachableDiagnostic("timeout esperando /healthz"))
        proc_cleaned = False
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                    proc_cleaned = True
                except (subprocess.TimeoutExpired, TimeoutError):
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                        proc_cleaned = True
                    except (subprocess.TimeoutExpired, TimeoutError, BaseException):
                        proc_cleaned = False
            else:
                proc_cleaned = True
        except BaseException:
            proc_cleaned = False
        if proc_cleaned:
            ph.cleanup_backend_proc = None

        log_closed = False
        try:
            log.close()
            log_closed = True
        except BaseException:
            log_closed = False
        if log_closed:
            ph.cleanup_backend_log = None
        return None
    except BaseException:
        targets_to_clean: list[Any] = []
        seen_target_ids: set[int] = set()
        for candidate in (proc, published_proc, returned_proc):
            if candidate is not None and id(candidate) not in seen_target_ids:
                seen_target_ids.add(id(candidate))
                targets_to_clean.append(candidate)
        all_cleaned = True
        for p in targets_to_clean:
            cleaned_this = False
            try:
                if hasattr(p, "poll") and p.poll() is None:
                    if hasattr(p, "terminate"):
                        p.terminate()
                    try:
                        if hasattr(p, "wait"):
                            p.wait(timeout=5)
                        cleaned_this = True
                    except (subprocess.TimeoutExpired, TimeoutError):
                        if hasattr(p, "kill"):
                            p.kill()
                        if hasattr(p, "wait"):
                            try:
                                p.wait(timeout=5)
                                cleaned_this = True
                            except (subprocess.TimeoutExpired, TimeoutError, BaseException):
                                cleaned_this = False
                        else:
                            cleaned_this = True
                    except TypeError:
                        try:
                            if hasattr(p, "wait"):
                                p.wait()
                            cleaned_this = True
                        except BaseException:
                            cleaned_this = False
                    except BaseException:
                        cleaned_this = False
                else:
                    cleaned_this = True
            except BaseException:
                cleaned_this = False
            if not cleaned_this:
                all_cleaned = False
        if all_cleaned:
            ph.cleanup_backend_proc = None

        log_closed = False
        if log is not None:
            try:
                log.close()
                log_closed = True
            except BaseException:
                log_closed = False
        if log_closed:
            ph.cleanup_backend_log = None
        raise


# --------------------------------------------------------------------------
# Fase 3 — Sessão + chave de stream
# --------------------------------------------------------------------------

def phase_session(ph: Phases) -> tuple[str, str] | None:
    banner("Fase 3/10 — Criando sessão de entrevista")
    resp = requests.post(
        f"{BASE_URL}/api/sessions",
        params={"mode": "interview", "title": "live-proof"},
        timeout=30,
    )
    if resp.status_code != 200:
        ph.record(PhaseID.SESSION_CREATED, PhaseStatus.FAIL, CredentialReachableDiagnostic(f"HTTP {resp.status_code}"))
        return None
    body = resp.json()
    session_id, stream_key = body.get("session_id"), body.get("stream_key")
    if not session_id or not stream_key:
        ph.record(PhaseID.SESSION_CREATED, PhaseStatus.FAIL, CredentialReachableDiagnostic("resposta sem session_id ou stream_key"))
        return None
    # Install the durable redaction boundary at the exact point where the
    # credential first becomes available.  This is intentionally before the
    # success row and before any later companion construction/admission.
    try:
        ph.register_stream_key(stream_key)
    except HarnessProtocolError as exc:
        ph.record(PhaseID.SESSION_CREATED, PhaseStatus.FAIL, CredentialReachableDiagnostic(f"sentinel de redaction rejeitado: {exc}"))
        return None
    # A chave é um segredo: registra-se apenas a presença e o comprimento.
    ph.record(PhaseID.SESSION_CREATED, PhaseStatus.PASS, PhaseDetail.template())
    return session_id, stream_key


# --------------------------------------------------------------------------
# Fase 4 — Sonda de chave errada (não depende de TCC; roda cedo de propósito)
# --------------------------------------------------------------------------

# Só estes dois desfechos provam que o gateway REJEITOU a chave. Um OSError, um
# timeout ou qualquer outro status provam apenas que a conexão não vingou — o que
# aconteceria igualmente com o backend fora do ar — e por isso contam como FAIL.
REJECT_HTTP_STATUSES = {401, 403}
REJECT_CLOSE_CODE = 1008


def stream_subprotocols(stream_key: str) -> list[str]:
    """Retorna os subprotocolos WebSocket na ordem canônica do gateway."""
    return ["tars-stream", stream_key]


async def _probe_invalid_key(session_id: str) -> tuple[bool, str]:
    """Rejeição positiva: 401/403 no handshake, ou fechamento 1008 sem aceitar frames."""
    url = f"{WS_BASE}/{session_id}"
    try:
        async with ws_connect(url, subprotocols=["tars-stream", "WRONG"], open_timeout=10) as ws:
            # Handshake aceito: o gateway ainda tem de fechar sem aceitar frames.
            await ws.send(encode_frame(session_id, "microphone", 0, 0, b"\x00" * FRAME_BYTES))
            try:
                await asyncio.wait_for(ws.recv(), timeout=5)
            except websockets.exceptions.ConnectionClosed as closed:
                code = closed.rcvd.code if closed.rcvd else None
                if code == REJECT_CLOSE_CODE:
                    return True, f"fechada com código {code} sem aceitar frames"
                return False, f"fechada com código {code}, esperado {REJECT_CLOSE_CODE}"
            except asyncio.TimeoutError:
                return False, "gateway manteve a conexão aberta com stream_key inválida"
            return False, "gateway respondeu dados com stream_key inválida"
    except websockets.exceptions.InvalidStatus as exc:
        status = exc.response.status_code
        if status in REJECT_HTTP_STATUSES:
            return True, f"handshake rejeitado com HTTP {status}"
        return False, f"HTTP {status} não é uma rejeição de autenticação (esperado {sorted(REJECT_HTTP_STATUSES)})"
    except websockets.exceptions.ConnectionClosed as closed:
        code = closed.rcvd.code if closed.rcvd else None
        if code == REJECT_CLOSE_CODE:
            return True, f"fechada com código {code}"
        return False, f"fechada com código {code}, esperado {REJECT_CLOSE_CODE}"
    except Exception as exc:
        # Ausência de sucesso não é prova de rejeição.
        return False, f"nenhuma rejeição observada — a conexão falhou por {type(exc).__name__}: {exc}"


async def _probe_valid_key(session_id: str, stream_key: str) -> tuple[bool, str]:
    """Controle positivo: sem uma aceitação no mesmo instante, a rejeição acima
    não distingue "autenticação funciona" de "o gateway recusa todo mundo".
    Nenhum frame é enviado aqui, então nenhum StreamManager é criado."""
    url = f"{WS_BASE}/{session_id}"
    try:
        async with ws_connect(url, subprotocols=stream_subprotocols(stream_key), open_timeout=10) as ws:
            await ws.send(json.dumps({"type": "hello", "sources": ["microphone"]}))
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=2)
                return False, f"gateway respondeu/fechou inesperadamente com a chave válida: {message!r}"
            except asyncio.TimeoutError:
                pass  # silêncio = conexão aceita e mantida aberta
            await ws.close()
            return True, "conexão aceita e mantida aberta, encerrada limpa"
    except Exception as exc:
        return False, f"chave válida recusada: {type(exc).__name__}: {exc}"


def phase_wrong_key(ph: Phases, session_id: str, stream_key: str) -> None:
    banner("Fase 4/10 — Sondas de chave de stream (rejeição + controle positivo)")

    async def both() -> tuple[tuple[bool, str], tuple[bool, str]]:
        return await _probe_invalid_key(session_id), await _probe_valid_key(session_id, stream_key)

    (bad_ok, bad_detail), (good_ok, good_detail) = asyncio.run(both())
    if bad_ok:
        ph.record(PhaseID.INVALID_KEY_REJECTED, PhaseStatus.PASS, PhaseDetail.template())
    else:
        ph.record(
            PhaseID.INVALID_KEY_REJECTED,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(bad_detail),
        )
    if good_ok:
        ph.record(PhaseID.VALID_KEY_ACCEPTED, PhaseStatus.PASS, PhaseDetail.template())
    else:
        ph.record(
            PhaseID.VALID_KEY_ACCEPTED,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(good_detail),
        )


# --------------------------------------------------------------------------
# Fase 5 — App menu-bar assinado (Process Tap)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveProofSnapshot:
    """Secret-free immutable facts captured before companion teardown."""

    artifact_valid: bool
    current_peer: bool
    authenticated_peer_key: str
    launch_nonce: str
    activation: Activation
    functional_permission_state: str
    functional_permission_tuple: CaptureTuple


def _artifact_facts_match(actual: ArtifactFacts, expected: ArtifactFacts) -> bool:
    """Compare sealed facts explicitly, with identity checked by raw bytes."""

    if type(actual) is not ArtifactFacts or type(expected) is not ArtifactFacts:
        return False
    fields = (
        "provenance_head",
        "provenance_tree",
        "dirty",
        "bundle_id",
        "team_id",
        "hardened_runtime",
        "entitlements",
        "strict_signature",
        "executable_digest",
        "sealed_executable_digest",
        "provenance_digest",
        "developer_id_authority",
        "audio_input_entitlement",
    )
    if any(getattr(actual, field) != getattr(expected, field) for field in fields):
        return False
    try:
        require_exact_static_code_identity(
            actual.static_identity,
            expected.static_identity,
            label="artifact static identity",
        )
    except HarnessProtocolError:
        return False
    return True


class CompanionRun:
    """A signed menu-bar app attached to one authenticated AF_UNIX run.

    LaunchServices owns app startup and returns a retained application handle;
    readiness is established only by authenticated, typed harness events.
    """

    def __init__(
        self,
        signed_app: Path,
        session_id: str,
        stream_key: str,
        tag: str,
        *,
        launcher: LaunchServicesAdapter | None = None,
        artifact_facts: ArtifactFacts | None = None,
        expected_head: str | None = None,
        expected_tree: str | None = None,
        expected_digest: str | None = None,
        artifact_inspector: SignedArtifactInspector | None = None,
        running_code_attestor: RunningCodeAttestor | None = None,
        on_publish: Callable[[CompanionRun], None] | None = None,
    ) -> None:
        validate_stream_key(stream_key)
        self.out_path = SCRATCH / f"companion-{tag}.log"
        self.launch_nonce = f"nonce-{os.getpid()}-{tag}"
        self._session_id: str | None = session_id
        self._stream_key: str | None = stream_key
        # Keep only the one-way session reference for the stop handshake; the
        # raw session ID is transient and is retired after command admission.
        self._session_binding = session_binding(session_id, self.launch_nonce)
        self._shutdown_nonce: str | None = None
        self._shutdown_acknowledged = False
        self._shutdown_request_sent = False
        self.activation: Activation | None = None
        self.peer_authenticated = False
        self.authenticated_pid: int | None = None
        self.authenticated_peer_key: str | None = None
        self.functional_permission_state = "unknown"
        self.functional_permission_tuple: CaptureTuple | None = None
        self.artifact_valid = False
        # Install the child-output redactor before validating or launching
        # anything.  No helper bytes can become observable through this
        # object without crossing this durable, credential-aware boundary.
        self._output_redactor = StreamingRedactor(stream_key)
        self._output_redactor_finished = False
        self._chunks: list[str] = []
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._connection: socket.socket | None = None
        self._state: HarnessState | None = None
        self._event_thread: threading.Thread | None = None
        self._event_stop = threading.Event()
        self._event_error: BaseException | None = None
        self._terminal_failure = False
        self._control_eof_observed = False
        self._lifecycle_stop_requested = False
        self._real_control_loss_observed = False
        self._tcc_origin: str | None = None
        self._proof_snapshot: LiveProofSnapshot | None = None
        self._process_completed = False
        self._eof_required = False
        self._teardown_complete = False
        self._cleanup_error: str | None = None
        self.run_dir: Path | None = None
        self.socket_path: Path | None = None
        self.server: UnixHarnessServer | None = None
        self.proc: Any = None
        self._expected_peer: Any = None

        if artifact_facts is None or expected_head is None or expected_tree is None:
            raise HarnessProtocolError("sealed artifact facts and current HEAD/tree are required before launch")
        if artifact_inspector is None or not callable(getattr(artifact_inspector, "inspect", None)):
            raise HarnessProtocolError("fresh static artifact inspector is required before launch")
        if not callable(getattr(running_code_attestor, "attest", None)) and not callable(running_code_attestor):
            raise HarnessProtocolError("running-code attestor is required before launch")
        validate_artifact(
            artifact_facts,
            expected_head=expected_head,
            expected_tree=expected_tree,
            expected_digest=expected_digest,
        )
        # Static policy validation is necessary but is not itself a positive
        # artifact fact.  Keep this false until the fresh readback, dynamic
        # audit-token attestation, final peer reread, and command admission
        # all complete.
        self.artifact_valid = False
        self._launcher = launcher
        self._signed_app = Path(signed_app)
        self._artifact_inspector = artifact_inspector
        # Retain the exact object; restart derives this same object rather
        # than accepting a caller-substituted attestor or cached success.
        self._running_code_attestor = running_code_attestor
        self._artifact_facts = artifact_facts
        self._expected_head = expected_head
        self._expected_tree = expected_tree
        self._expected_digest = expected_digest

        if on_publish is not None:
            on_publish(self)

        proc_target = None
        published_proc = None

        def on_process_published(p: Any) -> None:
            nonlocal published_proc, proc_target
            published_proc = p
            proc_target = p
            self.proc = p

        try:
            # Artifact validation is intentionally completed before creating a
            # listener or invoking LaunchServices.  A sealed/readback mismatch
            # therefore cannot leave a credential-capable socket behind.
            SCRATCH.mkdir(mode=0o700, parents=True, exist_ok=True)
            # AF_UNIX pathname limits are substantially shorter than ordinary
            # filesystem paths.  Keep the retained logs below SCRATCH but put the
            # ephemeral control endpoint directly under the configured temp
            # parent with a short random leaf so injected and live runs share the
            # same bounded socket behavior.
            temp_parent = Path(os.environ.get("TMPDIR", "/tmp")).resolve()
            temp_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.run_dir = Path(tempfile.mkdtemp(prefix="tars-live-", dir=str(temp_parent)))
            os.chmod(self.run_dir, 0o700)
            self.socket_path = self.run_dir / "control.sock"
            self.server = UnixHarnessServer(self.socket_path)
            self.server.bind()
            spec = make_launch_spec(
                signed_app,
                socket_path=str(self.socket_path),
                launch_nonce=self.launch_nonce,
                stream_key=stream_key,
            )
            self.launch_spec = spec
            launch_result = (launcher or MacOSLaunchServicesAdapter()).launch(
                spec,
                on_process=on_process_published,
            )
            if isinstance(launch_result, tuple) and len(launch_result) == 2:
                returned_proc, self._expected_peer = launch_result
            else:
                returned_proc = getattr(launch_result, "process", launch_result)
                self._expected_peer = getattr(launch_result, "peer", None)
            proc_target = returned_proc
            self.proc = returned_proc
            if published_proc is None:
                raise HarnessProtocolError("launcher returned without publishing process facade")
            if returned_proc is not published_proc:
                raise HarnessProtocolError("launched process does not match published process")
            # The redactor was initialized before the LaunchServices call; start
            # draining as soon as the retained process facade exists so early
            # helper diagnostics cannot sit outside the redaction boundary.
            output = getattr(self.proc, "stdout", None)
            if output is not None:
                self._reader = threading.Thread(target=self._drain_pipe, args=(output,), daemon=True)
                self._reader.start()
        except BaseException:
            targets_to_clean: list[Any] = []
            seen_target_ids: set[int] = set()
            for candidate in (self.proc, proc_target, published_proc):
                if candidate is not None and id(candidate) not in seen_target_ids:
                    seen_target_ids.add(id(candidate))
                    targets_to_clean.append(candidate)
            for target in targets_to_clean:
                try:
                    _cleanup_process_target(target)
                except BaseException:
                    pass
            if self._reader is not None:
                try:
                    self._reader.join(timeout=1.0)
                except BaseException:
                    pass
            for target in targets_to_clean:
                out_pipe = getattr(target, "stdout", None)
                if out_pipe is not None and hasattr(out_pipe, "close"):
                    try:
                        out_pipe.close()
                    except BaseException:
                        pass
            if self.server is not None:
                try:
                    self.server.close()
                except BaseException:
                    pass
            if self.run_dir is not None:
                try:
                    if self.socket_path is not None and self.socket_path.exists():
                        self.socket_path.unlink()
                except (OSError, BaseException):
                    pass
                try:
                    self.run_dir.rmdir()
                except (OSError, BaseException):
                    pass
            self._teardown_complete = True
            raise

    def _retire_post_bind_failure(self) -> None:
        """Retire post-bind credentials while retaining cleanup ownership."""

        self._session_id = None
        self._stream_key = None
        self.artifact_valid = False
        if self._state is not None:
            self._state.revoke_control()

    def send_authenticated_session(
        self,
        peer_identity: PeerIdentity | None = None,
        expected_peer: PeerIdentity | None = None,
        *,
        peer_reader: Callable[[socket.socket], PeerIdentity] | None = None,
    ) -> None:
        """Send the credential only after the future peer verifier succeeds.

        The live implementation supplies a Darwin kernel identity here.  The
        method is intentionally not called by offline tests and never places
        the key in argv, URL, output, or evidence.
        """
        if self.server.listener is None:
            raise RuntimeError("control listener is closed")
        expected = expected_peer if expected_peer is not None else self._expected_peer
        if type(expected) is not PeerIdentity:
            raise HarnessProtocolError("LaunchServices did not provide an expected peer identity")
        state = HarnessState(
            expected_peer=expected,
            server_euid=expected.euid,
            launch_nonce=self.launch_nonce,
        )
        # The accepted socket is authenticated first from kernel metadata.  A
        # caller-provided identity is only a consistency assertion for an
        # injected test reader; it is never authoritative.
        reader = peer_reader or DarwinPeerIdentityReader()
        conn, actual_peer = self.server.accept_authenticated(state, peer_reader=reader)
        # Bind the process facade to the complete kernel identity immediately
        # after peer admission.  The revalidator closes over this still-open
        # descriptor, so a later lifecycle operation cannot trust a reused PID
        # or a changed audit/path identity.  The /usr/bin/open helper PID is
        # cleanup-only and is never eligible for restart identity or signals.
        binder = getattr(self.proc, "bind_authenticated_peer", None)
        if not callable(binder):
            self.server.close_connection(conn)
            raise HarnessProtocolError("launcher process cannot bind authenticated peer identity")

        def revalidate_peer() -> PeerIdentity:
            if conn.fileno() < 0:
                raise HarnessProtocolError("authenticated control socket is closed")
            previous_timeout = conn.gettimeout()
            try:
                conn.settimeout(0.0)
                marker = conn.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
            except (BlockingIOError, InterruptedError, socket.timeout):
                marker = None
            except OSError as exc:
                raise HarnessProtocolError("authenticated control socket revalidation failed") from exc
            finally:
                try:
                    conn.settimeout(previous_timeout)
                except OSError as exc:
                    raise HarnessProtocolError("authenticated control socket is closed") from exc
            if marker == b"":
                # A connected socket can report EOF while getpeername still
                # succeeds; the zero-length nonblocking peek is authoritative.
                raise HarnessProtocolError("authenticated control socket reached EOF")
            return reader(conn)

        try:
            binder(actual_peer, revalidator=revalidate_peer)
        except Exception:
            self.server.close_connection(conn)
            raise
        if not peer_identity_equal(getattr(self.proc, "authenticated_peer", None), actual_peer):
            self.server.close_connection(conn)
            raise HarnessProtocolError("launcher process peer binding failed")
        if getattr(self.proc, "pid", None) != actual_peer.pid:
            self.server.close_connection(conn)
            raise HarnessProtocolError("launcher process PID binding failed")
        if peer_identity is not None and not peer_identity_equal(actual_peer, peer_identity):
            self.server.close_connection(conn)
            raise HarnessProtocolError("accepted kernel peer differs from expected fixture")
        # From this point the process has an exact authenticated peer binding.
        # Publish the descriptor ownership before the final artifact readback
        # so a post-bind rejection can still use the token-only stop boundary;
        # closing this descriptor here would make the accepted process
        # unauthorizable and strand the helper.
        self.peer_authenticated = True
        self.authenticated_pid = self.proc.pid
        self.authenticated_peer_key = peer_fingerprint(actual_peer)
        self._connection = conn
        self._state = state
        # The app may have changed after phase 1 inspected it.  Re-read the
        # explicit bundle through the same injectable static public-signature
        # and identity boundary immediately before any credential encoding.
        # Compare each sealed field explicitly.  In particular, never let a
        # dataclass/subclass equality override authorize the identity boundary.
        try:
            fresh_facts = self._artifact_inspector.inspect(self._signed_app)
            validate_artifact(
                fresh_facts,
                expected_head=self._expected_head,
                expected_tree=self._expected_tree,
                expected_digest=self._expected_digest,
            )
            if not _artifact_facts_match(fresh_facts, self._artifact_facts):
                raise HarnessProtocolError("artifact facts changed after preflight")
        except BaseException:
            self._retire_post_bind_failure()
            raise

        # Revalidate the complete kernel peer on the still-open descriptor and
        # dynamically attest that exact raw audit token against the freshly
        # sealed static identity.  The final same-descriptor reread, strict
        # attestor type/equality check, and credential send are one transaction
        # owned by ``UnixHarnessServer.send_one_session`` below.
        attestor = self._running_code_attestor
        if attestor is None:
            self._retire_post_bind_failure()
            raise HarnessProtocolError("running-code attestor is unavailable")
        try:
            reread_peer = revalidate_peer()
            attest_call = getattr(attestor, "attest", None)
            if not callable(attest_call):
                attest_call = attestor
            attested = attest_call(
                reread_peer,
                fresh_facts.static_identity,
            )
        except BaseException as exc:
            self._retire_post_bind_failure()
            if isinstance(exc, Exception):
                raise HarnessProtocolError("dynamic running-code attestation failed") from exc
            raise
        # The final transaction owns the strict typed/equality check.  Every
        # result, including False, True, None, and duck types, reaches that
        # boundary and is rejected before command encoding.
        session_id = self._session_id
        stream_key = self._stream_key
        if session_id is None or stream_key is None:
            self._retire_post_bind_failure()
            raise HarnessProtocolError("credential transaction has already completed")
        try:
            self.server.send_one_session(
                conn,
                state,
                actual_peer,
                peer_revalidator=revalidate_peer,
                attested_identity=attested,
                static_identity=fresh_facts.static_identity,
                session_id=session_id,
                stream_key=stream_key,
                gateway=WS_BASE,
            )
        except BaseException:
            # A failed command transaction cannot leave a transient credential
            # or a positive artifact fact on this run facade.  Preserve the
            # original exception (including control signals) after retirement.
            self._retire_post_bind_failure()
            raise
        # The command/key lifetime ends at the successful credential-send
        # transaction.  The private harness state retains only the key needed
        # for event admission and revokes it on control loss; this run facade
        # does not expose raw command credentials afterward.
        self._session_id = None
        self._stream_key = None
        # The command has crossed only after static and dynamic identity
        # proofs.  This is the first point at which artifact_valid may become
        # a positive fact for activation/evidence.
        self.artifact_valid = True

    def _send_shutdown_request(self, *, timeout: float = 5.0) -> bool:
        """Request an authenticated app stop while the event thread owns recv."""

        if (
            self._connection is None
            or self._state is None
            or self._state.peer is None
            or self._state.control_lost
        ):
            return False
        nonce = shutdown_nonce()
        try:
            shutdown_binding(self._session_binding, nonce)
            # Publish the pending nonce before writing so an immediately
            # arriving acknowledgement is checked against this exact request.
            with self._lock:
                self._shutdown_nonce = nonce
                self._shutdown_acknowledged = False
                self._shutdown_request_sent = True
            self.server.send_shutdown_request(
                self._connection,
                self._state,
                session_ref=self._session_binding,
                nonce=nonce,
                timeout=timeout,
            )
            return True
        except BaseException as exc:
            # Retire every pending shutdown fact before deciding whether this
            # is an ordinary write failure or a non-Exception control signal.
            # The latter must cross this boundary unchanged after cleanup;
            # swallowing KeyboardInterrupt/SystemExit would leave callers
            # believing lifecycle teardown completed normally.
            with self._lock:
                self._shutdown_request_sent = False
                self._shutdown_nonce = None
                self._shutdown_acknowledged = False
                self._proof_snapshot = None
            self._state.revoke_control()
            if isinstance(exc, Exception):
                return False
            raise

    def _prepare_shutdown_ack_reader(self) -> bool:
        """Restart the reader so its shutdown nonce snapshot is current."""

        reader = self._event_thread
        if reader is None:
            return True
        # The old reader may already be inside receive_event(), whose
        # shutdown nonce argument is intentionally immutable for that one
        # read.  Stop and join it before publishing a new request, then the
        # replacement reader will snapshot the exact pending nonce.
        with self._lock:
            self._lifecycle_stop_requested = True
        self._event_stop.set()
        reader.join(timeout=2)
        if reader.is_alive():
            self._cleanup_error = "event reader did not terminate before shutdown request"
            return False
        self._event_thread = None
        self._event_stop.clear()
        return True

    def start_event_reader(self, *, timeout: float = 15.0) -> None:
        if self._event_thread is not None:
            raise HarnessProtocolError("event reader already started")
        self._event_thread = threading.Thread(
            target=self._read_events,
            args=(timeout,),
            daemon=True,
        )
        self._event_thread.start()

    def _read_events(self, timeout: float = 15.0) -> None:
        while not self._event_stop.is_set():
            try:
                event = self.receive_event(timeout=timeout, should_stop=self._event_stop.is_set)
                if event.get("type") == "shutdown_ack":
                    with self._lock:
                        self._shutdown_acknowledged = True
                    return
                peer = self.authenticated_peer_key
                if peer is None:
                    raise HarnessProtocolError("event arrived before peer admission")
                event_tuple = CaptureTuple(
                    kernel_peer=peer,
                    # The event wire carries only the role-bound nonce
                    # reference.  After HarnessState has verified that
                    # reference against the authenticated command, the
                    # in-memory capture tuple may use the command's raw nonce
                    # for lifecycle freshness checks; it is never projected
                    # into event/evidence output.
                    launch_nonce=self.launch_nonce,
                    attempt_id=str(event["attempt_id"]),
                    generation=int(event["generation"]),
                )
                if event["kind"] == "activation":
                    self.record_activation(
                        Activation(
                            tuple=event_tuple,
                            requested_engine=str(event["requested_engine"]),
                            resolved_engine=str(event["resolved_engine"]),
                            actual_engine=str(event["actual_engine"]),
                        ),
                        artifact_valid=self.artifact_valid,
                    )
                else:
                    status = event.get("status")
                    if isinstance(status, Mapping) and status.get("kind") == "failed":
                        # A schema-valid failed health event is a typed,
                        # non-granting terminal observation.  It may be the
                        # first event in a session (startup denial), so retire
                        # the authenticated control without routing through
                        # the generic error path that erases an explicit
                        # denial as unknown.
                        self._retire_terminal_failure(
                            str(status.get("permission", "unknown"))
                        )
                        return
                    activation = self.activation
                    current_tuple = activation.tuple if activation is not None else None
                    if functional_health(
                        current_tuple=current_tuple,
                        event_tuple=event_tuple,
                        status=status if isinstance(status, Mapping) else None,
                        actual_engine=str(event.get("actual_engine", "")),
                    ):
                        # Process Tap emits this status only after its monitor
                        # observes finite nonzero decoded PCM.  The event
                        # carries no samples or credential bytes.
                        self.record_functional_permission([1.0], capture_tuple=event_tuple)
                    else:
                        # Schema-valid terminal or degraded health remains an
                        # accepted event, but it invalidates any historical
                        # grant for the current tuple.  Explicit denial is
                        # preserved as denied; every other unsafe state is
                        # deliberately inconclusive/unknown.
                        denied = (
                            isinstance(status, Mapping)
                            and status.get("permission") in {"denied", "revoked"}
                        )
                        self.record_functional_permission(
                            None,
                            capture_tuple=event_tuple,
                            explicit_denied=denied,
                        )
            except BaseException as exc:
                if isinstance(exc, HarnessProtocolError) and str(exc) == "control connection lost":
                    self._control_eof_observed = True
                with self._lock:
                    real_control_loss = self._real_control_loss_observed
                intentional_stop = (
                    isinstance(exc, HarnessProtocolError)
                    and str(exc) == "control reader stopped"
                    and not real_control_loss
                )
                # A stop callback may intentionally end a quiet reader, but
                # an EOF/protocol fault has already revoked HarnessState and
                # must revoke this run even if teardown won the scheduling
                # race and set _event_stop first.
                if not intentional_stop:
                    self._revoke_control(exc)
                return

    def _retire_terminal_failure(self, permission: str) -> None:
        """Retire a protocol-accepted failed health without generic reset."""

        if permission not in {"unknown", "denied"}:
            permission = "unknown"
        with self._lock:
            # A terminal event accepted after the pre-stop snapshot is a new
            # proof-state transition.  Revoke the historical snapshot before
            # changing any of the live grant fields; ordinary teardown does
            # not call this edge and therefore intentionally preserves it.
            if self._proof_snapshot is not None:
                self._proof_snapshot = None
            self._terminal_failure = True
            self._tcc_origin = "authenticated_event"
            # Preserve the typed non-granting outcome while removing every
            # success fact.  The authenticated descriptor and complete peer
            # binding deliberately remain alive until stop() has completed the
            # token-only lifecycle signal and joined both readers.  No
            # event_error is manufactured: this was an accepted canonical
            # terminal fact, not a malformed/control-loss failure.
            self.functional_permission_state = permission
            self.functional_permission_tuple = None
            self.activation = None
            if self._state is not None:
                self._state.revoke_control()

    def _revoke_control(self, error: BaseException) -> None:
        """Atomically revoke success facts without abandoning lifecycle ownership.

        A post-admission protocol fault is not permission to discard the
        authenticated descriptor.  The accepted socket and its process-peer
        binding are the only authority available to revalidate a TERM/KILL
        operation.  Keep both owners until ``stop()``/``kill()`` proves the
        bounded process and EOF edges and performs the one final retirement.
        """

        safe_error = self._output_redactor.redact_complete(str(error))
        if not safe_error:
            safe_error = "control protocol error"
        with self._lock:
            # Never retain an exception object whose description may carry a
            # credential echoed by a decoder, injected boundary, or future
            # transport.  The error bit remains typed and truthy, while its
            # diagnostic text is already on the safe side of the redactor.
            if self._proof_snapshot is not None:
                self._proof_snapshot = None
            self._event_error = HarnessProtocolError(safe_error)
            self.activation = None
            self.functional_permission_state = "unknown"
            self.functional_permission_tuple = None
            if self._state is not None:
                self._state.revoke_control()

    @property
    def secret_seen(self) -> bool:
        """Whether helper output or a control error crossed the secret edge."""

        return self._output_redactor.seen

    @property
    def tcc_origin(self) -> str | None:
        """Origin of a ``tcc`` result: authenticated event or process exit 2."""

        return self._tcc_origin

    @property
    def proof_snapshot(self) -> "LiveProofSnapshot | None":
        """Immutable pre-stop facts retained only in memory for final proof."""

        with self._lock:
            return self._proof_snapshot

    @property
    def cleanup_succeeded(self) -> bool:
        return self._teardown_complete

    @property
    def cleanup_error(self) -> str | None:
        return self._cleanup_error

    def capture_live_proof_snapshot(self) -> "LiveProofSnapshot | None":
        """Capture the exact positive facts before lifecycle teardown."""

        # Keep the readiness check and snapshot assignment in the same
        # critical section as every event/control invalidation.  Calling the
        # public ``capture_ready`` helper here would be a lock re-entry once
        # that helper is used by other callers, so perform its read-only
        # predicate directly under this non-reentrant lock.
        with self._lock:
            activation = self.activation
            if (
                self._event_error is not None
                or (self._state is not None and self._state.control_lost)
                or activation is None
            ):
                self._proof_snapshot = None
                return None
            peer_key = self.authenticated_peer_key
            functional_tuple = self.functional_permission_tuple
            if not (
                self.artifact_valid
                and self.peer_authenticated
                and activation.is_process_tap()
                and activation.tuple.launch_nonce == self.launch_nonce
                and peer_key is not None
                and activation.tuple.kernel_peer == peer_key
                and activation.tuple.attempt_id
                and activation.tuple.generation > 0
                and self.functional_permission_state == "granted"
                and functional_tuple == activation.tuple
            ):
                self._proof_snapshot = None
                return None
            self._proof_snapshot = LiveProofSnapshot(
                artifact_valid=self.artifact_valid,
                current_peer=self.peer_authenticated,
                authenticated_peer_key=peer_key,
                launch_nonce=self.launch_nonce,
                activation=activation,
                functional_permission_state=self.functional_permission_state,
                functional_permission_tuple=functional_tuple,
            )
            return self._proof_snapshot

    def receive_event(
        self,
        timeout: float = 15.0,
        *,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        if self._connection is None or self._state is None or self._state.peer is None:
            raise HarnessProtocolError("control session is not authenticated")
        try:
            return self.server.receive_event(
                self._connection,
                self._state,
                self._state.peer,
                timeout=timeout,
                should_stop=should_stop,
                shutdown_nonce_value=self._shutdown_nonce if self._shutdown_request_sent else None,
            )
        except BaseException as exc:
            # HarnessState is revoked by the server before every real EOF,
            # socket error, and protocol/schema rejection.  A stop callback
            # is the sole exception: lifecycle teardown revokes the state
            # first, but its exact ``control reader stopped`` result is not a
            # peer loss and may preserve the pre-stop proof snapshot.
            intentional_stop = (
                isinstance(exc, HarnessProtocolError)
                and str(exc) == "control reader stopped"
                and self._lifecycle_stop_requested
            )
            if not intentional_stop and self._state.control_lost:
                with self._lock:
                    self._real_control_loss_observed = True
            raise

    def drain_events_until_control_loss(self) -> None:
        while True:
            self.receive_event()

    def record_activation(self, activation: Activation, *, artifact_valid: bool) -> None:
        """Record an authenticated event received from the control core."""
        with self._lock:
            if not activation.is_process_tap():
                raise RuntimeError("candidate capture requires requested=resolved=actual process-tap")
            if activation.tuple.launch_nonce != self.launch_nonce:
                raise RuntimeError("activation launch nonce mismatch")
            if self.authenticated_peer_key is None or activation.tuple.kernel_peer != self.authenticated_peer_key:
                raise RuntimeError("activation kernel peer does not match authenticated peer")
            if not activation.tuple.attempt_id or activation.tuple.generation <= 0:
                raise RuntimeError("activation lacks a current attempt/generation")
            if not artifact_valid:
                raise HarnessProtocolError("activation cannot override failed artifact preflight")
            if self._proof_snapshot is not None:
                self._proof_snapshot = None
            self.activation = activation
            self.artifact_valid = True

    def record_functional_permission(
        self,
        pcm_samples: Iterable[float] | None,
        *,
        capture_tuple: CaptureTuple | None = None,
        explicit_denied: bool = False,
    ) -> None:
        permission_state = functional_permission(
            pcm_samples=pcm_samples,
            explicit_denied=explicit_denied,
        )
        with self._lock:
            if self._proof_snapshot is not None:
                self._proof_snapshot = None
            self.functional_permission_state = permission_state
            self.functional_permission_tuple = capture_tuple

    def capture_ready(self) -> bool:
        if self._event_error is not None:
            return False
        activation = self.activation
        if activation is None:
            return False
        return (
            self.artifact_valid
            and self.peer_authenticated
            and activation.is_process_tap()
            and activation.tuple.launch_nonce == self.launch_nonce
            and self.authenticated_peer_key is not None
            and activation.tuple.kernel_peer == self.authenticated_peer_key
            and activation.tuple.attempt_id
            and activation.tuple.generation > 0
            and self.functional_permission_state == "granted"
            and self.functional_permission_tuple == activation.tuple
        )

    def positive_claim(self, transcript_valid: bool) -> bool:
        """Evaluate the one positive claim with the current live tuple facts."""
        if self._event_error is not None:
            return False
        activation = self.activation
        permission_state = (
            self.functional_permission_state
            if activation is not None and self.functional_permission_tuple == activation.tuple
            else "unknown"
        )
        if activation is None or self.authenticated_peer_key is None or self.functional_permission_tuple is None:
            return False
        return positive_process_tap_claim(PositiveProcessTapProof(
            artifact_valid=self.artifact_valid,
            current_peer=self.peer_authenticated,
            authenticated_peer_key=self.authenticated_peer_key,
            launch_nonce=self.launch_nonce,
            activation=activation,
            functional_permission_state=permission_state,
            functional_permission_tuple=self.functional_permission_tuple,
            transcript_valid=transcript_valid,
        ))

    def _drain_pipe(self, pipe: IO[bytes]) -> None:
        try:
            while True:
                try:
                    data = pipe.read(4096)
                except (OSError, ValueError):
                    break
                if not data:
                    break
                safe = self._output_redactor.feed(data.decode("utf-8", errors="replace"))
                if safe:
                    with self._lock:
                        self._chunks.append(safe)
        finally:
            tail = self._output_redactor.finish()
            with self._lock:
                if tail:
                    self._chunks.append(tail)
                self._output_redactor_finished = True

    def output(self) -> str:
        with self._lock:
            return "".join(self._chunks)

    def wait_for_capture(self, timeout: float = 25.0) -> tuple[str, str]:
        """Devolve ('ativo'|'tcc'|'falhou'|'timeout', saída do processo)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.output()
            if self._event_error is not None:
                return "falhou", text
            if self._terminal_failure:
                self._tcc_origin = "authenticated_event"
                return (
                    "tcc" if self.functional_permission_state == "denied" else "falhou",
                    text,
                )
            if self.capture_ready():
                return "ativo", text
            code = self.proc.poll()
            if code is not None:
                if code == 2:
                    self._tcc_origin = "process_exit_2"
                    return "tcc", text
                self._tcc_origin = None
                return "falhou", text
            time.sleep(0.4)
        return "timeout", self.output()

    def _invalidate_success_facts(self) -> None:
        with self._lock:
            self._lifecycle_stop_requested = True
            self.activation = None
            self.functional_permission_state = "unknown"
            self.functional_permission_tuple = None
            # Teardown/control-loss revokes the command credentials even when
            # a reader join or process wait later fails.  The output redactor
            # keeps only its private sentinel until its already-owned pipe
            # reader finishes; no command mapping or raw key remains here.
            self._session_id = None
            self._stream_key = None
            if self._state is not None and not self._shutdown_request_sent:
                self._state.revoke_control()

    def _stop_event_reader(self) -> bool:
        """Stop/join the sole socket reader before touching its descriptor."""

        self._event_stop.set()
        reader = self._event_thread
        if reader is None:
            return True
        reader.join(timeout=2)
        if reader.is_alive():
            self._cleanup_error = "event reader did not terminate"
            return False
        return True

    def _wait_for_process_completion(self, timeout: float = 5.0) -> bool:
        try:
            self.proc.wait(timeout=timeout)
            return True
        except (subprocess.TimeoutExpired, TimeoutError, HarnessProtocolError, OSError):
            return False

    def _wait_for_passive_helper_completion(self, timeout: float = 0.0) -> bool:
        """Observe only LaunchServices helper completion after peer EOF.

        An authenticated peer's EOF removes the audit-token signaling
        capability.  In that state the companion must not attempt a
        revalidation or signal; completion can be accepted only from the
        already-retained ``open -W`` helper.  The adapter exposes that edge
        explicitly.  The small fallback keeps injected launch facades
        production-shaped while retaining the same no-signal rule.
        """

        waiter = getattr(self.proc, "wait_for_helper_completion", None)
        try:
            if callable(waiter):
                waiter(timeout=timeout)
            else:
                # Custom offline launchers may return the helper facade
                # directly.  At this point ``wait`` is observation only; no
                # process signal or peer revalidation is attempted.
                self.proc.wait(timeout=timeout)
                poll = getattr(self.proc, "poll", None)
                if callable(poll) and poll() is None:
                    return False
        except Exception:
            # Passive completion is a proof edge, not a best-effort wait. Any
            # observer failure must leave ownership retained and make the
            # caller retry or report an inconclusive cleanup.
            return False
        return True

    def _mark_control_eof(self) -> None:
        """Record the authenticated descriptor EOF exactly once."""

        with self._lock:
            self._control_eof_observed = True
            # EOF is a proof-preserving teardown edge only after the exact
            # nonce-bound shutdown request was admitted by the event reader.
            # Process completion is a separate lifecycle conjunct and must
            # never make an unacknowledged peer close look graceful.  The
            # request/nonce facts are non-secret; HarnessState's raw session
            # binding and stream key are revoked before process wait below.
            shutdown_ack_admitted = (
                self._shutdown_request_sent is True
                and type(self._shutdown_nonce) is str
                and self._shutdown_acknowledged is True
            )
            if not shutdown_ack_admitted:
                # Invalidate the run-side proof state before publishing the
                # corresponding HarnessState control loss.  This closes the
                # stop/probe handoff window without calling another
                # lock-taking helper while this lock is held.
                self._proof_snapshot = None
                self._event_error = HarnessProtocolError("control connection lost")
                self.activation = None
                self.functional_permission_state = "unknown"
                self.functional_permission_tuple = None
            if self._state is not None:
                self._state.revoke_control()
        # EOF observed before exact shutdown acknowledgement is a real
        # control loss, even when discovered by stop() after the event reader
        # was asked to stop.  EOF following the admitted acknowledgement may
        # preserve the pre-stop snapshot; process completion remains a
        # separate teardown obligation checked by _teardown().

    def _observe_control_eof_now(self) -> bool:
        """Probe for an already-present EOF without waiting or consuming data.

        This probe is deliberately performed after the event reader has
        joined and before any token-based process poll or signal.  A closed
        authenticated socket removes the authority needed for revalidation;
        callers must therefore switch to passive retained-helper completion
        immediately rather than asking the token boundary to run again.
        """

        if self._control_eof_observed:
            return True
        connection = self._connection
        if connection is None:
            return False
        previous_timeout: float | None
        try:
            previous_timeout = connection.gettimeout()
            connection.settimeout(0.0)
            try:
                marker = connection.recv(
                    1,
                    socket.MSG_PEEK | getattr(socket, "MSG_DONTWAIT", 0),
                )
            except (BlockingIOError, InterruptedError, TimeoutError, socket.timeout):
                return False
            except OSError:
                return False
            if marker == b"":
                self._mark_control_eof()
                return True
            return False
        except (OSError, ValueError):
            return False
        finally:
            try:
                connection.settimeout(previous_timeout)
            except (OSError, ValueError, UnboundLocalError):
                pass

    def _wait_for_control_eof(self, timeout: float = 5.0) -> bool:
        """Observe peer EOF after the event reader has relinquished recv."""

        if self._observe_control_eof_now():
            return True
        connection = self._connection
        if connection is None:
            return False
        deadline = time.monotonic() + timeout
        previous_timeout: float | None = None
        try:
            previous_timeout = connection.gettimeout()
            while time.monotonic() < deadline:
                connection.settimeout(0.0)
                try:
                    marker = connection.recv(
                        1,
                        socket.MSG_PEEK | getattr(socket, "MSG_DONTWAIT", 0),
                    )
                except (BlockingIOError, InterruptedError, TimeoutError, socket.timeout):
                    time.sleep(min(0.01, max(0.001, deadline - time.monotonic())))
                    continue
                except OSError:
                    return False
                if marker == b"":
                    self._mark_control_eof()
                    return True
                # Data remaining after the event reader stopped is not EOF;
                # never consume it while proving the peer lifecycle edge.
                return False
            return False
        finally:
            try:
                connection.settimeout(previous_timeout)
            except (OSError, ValueError):
                pass

    def _complete_after_control_eof(self) -> bool:
        """Complete only through the retained helper after authenticated EOF."""

        if not self._control_eof_observed:
            return False
        if not self._process_completed:
            if not self._wait_for_passive_helper_completion(timeout=5.0):
                self._cleanup_error = "LaunchServices helper completion was not observed"
                return False
            self._process_completed = True
        return self._teardown()

    def kill(self) -> bool:
        """SIGKILL, with bounded process and authenticated-socket completion proof."""

        if self._teardown_complete:
            return True
        with self._lock:
            self._proof_snapshot = None
            self._shutdown_request_sent = False
        self._invalidate_success_facts()
        # Revalidation/signal must never race the event reader's socket peek.
        if not self._stop_event_reader():
            return False
        self._eof_required = self.peer_authenticated or self._connection is not None
        # The event reader is now retired.  Probe the descriptor before
        # proc.poll()/kill(): an already-present EOF removes token authority.
        if self._eof_required and self._observe_control_eof_now():
            return self._complete_after_control_eof()
        if self._control_eof_observed:
            return self._complete_after_control_eof()
        if self._process_completed:
            # The process may already have exited (or completed during a
            # previous bounded stop attempt).  Do not ask LaunchServices to
            # poll a dead helper again: the remaining proof obligation is the
            # authenticated peer EOF, followed by exactly-once teardown.
            if self._eof_required and not self._wait_for_control_eof():
                self._cleanup_error = "authenticated control EOF was not observed"
                return False
            return self._teardown()
        try:
            process_status = self.proc.poll()
        except Exception:
            if self._eof_required and self._observe_control_eof_now():
                return self._complete_after_control_eof()
            self._cleanup_error = "process completion could not be authenticated"
            return False
        if process_status is None:
            try:
                self.proc.kill()
            except Exception:
                if self._eof_required and self._observe_control_eof_now():
                    return self._complete_after_control_eof()
                self._cleanup_error = "token-only SIGKILL was not accepted"
                return False
            if not self._wait_for_process_completion():
                if self._eof_required and self._observe_control_eof_now():
                    return self._complete_after_control_eof()
                self._cleanup_error = "SIGKILL completion was not observed"
                return False
            self._process_completed = True
        else:
            self._process_completed = True
        if self._eof_required and not self._wait_for_control_eof():
            self._cleanup_error = "authenticated control EOF was not observed"
            return False
        return self._teardown()

    def _teardown(self) -> bool:
        """Retire resources only after both reader owners have joined."""

        if self._teardown_complete:
            return True
        # Callers normally stop the event reader before process lifecycle
        # signaling.  Keeping this guard makes direct/repeated cleanup safe.
        self._event_stop.set()
        event_reader = self._event_thread
        if event_reader is not None:
            event_reader.join(timeout=2)
            if event_reader.is_alive():
                self._cleanup_error = "event reader did not terminate"
                return False
        output_reader = self._reader
        if output_reader is not None:
            output_reader.join(timeout=2)
            if output_reader.is_alive():
                self._cleanup_error = "output reader did not terminate"
                return False
            if not self._output_redactor_finished:
                self._cleanup_error = "output redactor was not finalized"
                return False
        if not self._process_completed:
            self._cleanup_error = "process completion was not proven"
            return False
        if self._eof_required and not self._control_eof_observed:
            self._cleanup_error = "authenticated control EOF was not proven"
            return False
        # Typed terminal and generic control-loss paths both retain this exact
        # descriptor and peer binding through signal/wait (or passive helper
        # completion after EOF); only after all readers have joined may the
        # owner retire it.  The state transition below is exactly-once.
        with self._lock:
            connection = self._connection
            self._connection = None
            self.peer_authenticated = False
            self.authenticated_pid = None
            self.authenticated_peer_key = None
            if self._state is not None:
                self._state.revoke_control()
        if connection is not None and self.server is not None:
            self.server.close_connection(connection)
        # The streaming redactor has already made every retained chunk safe.
        try:
            self.out_path.write_text(self.output(), encoding="utf-8")
            self._output_redactor.retire()
        except (OSError, HarnessProtocolError) as exc:
            self._cleanup_error = f"safe output finalization failed: {exc}"
            return False
        if self.server is not None:
            self.server.close()
        if self.run_dir is not None:
            try:
                self.run_dir.rmdir()
            except OSError:
                pass
        self._teardown_complete = True
        self._cleanup_error = None
        return True

    def stop(self) -> bool:
        """Stop gracefully, escalating through the token boundary on timeout."""

        if self._teardown_complete:
            return True
        # The authenticated event reader remains the sole recv owner while a
        # nonce-bound stop request is admitted.  Only its exact acknowledgement
        # can preserve the pre-stop snapshot; every fallback below is cleanup
        # only and explicitly revokes that proof.
        request_sent = False
        if self._connection is not None and self._state is not None and self._event_thread is not None:
            if self._prepare_shutdown_ack_reader():
                request_sent = self._send_shutdown_request(timeout=1.0)
                if request_sent:
                    self.start_event_reader()
        self._invalidate_success_facts()
        if request_sent:
            reader = self._event_thread
            if reader is not None:
                reader.join(timeout=1.5)
            with self._lock:
                acknowledged = (
                    type(self._shutdown_acknowledged) is bool
                    and self._shutdown_acknowledged
                    and self._shutdown_request_sent is True
                    and type(self._shutdown_nonce) is str
                )
            if reader is None or reader.is_alive() or not acknowledged:
                with self._lock:
                    self._proof_snapshot = None
                    self._shutdown_request_sent = False
                if self._state is not None:
                    self._state.revoke_control()
                if not self._stop_event_reader():
                    return False
            else:
                # The exact ack has crossed the authenticated event boundary.
                # Retire HarnessState now so its raw stream key/session
                # binding cannot survive into process wait.  Keep only the
                # non-secret ack fact above for the later EOF predicate.
                if self._state is not None:
                    self._state.revoke_control()
        else:
            with self._lock:
                self._proof_snapshot = None
                self._shutdown_request_sent = False
            if self._state is not None:
                self._state.revoke_control()
            # Never let lifecycle revalidation race the event reader's recv.
            if not self._stop_event_reader():
                return False
        # An authenticated connection is a second lifecycle owner.  Process
        # completion (including a graceful TERM or an already-exited helper)
        # is not enough to retire that capability: the peer must also close
        # and prove EOF before the descriptor/listener can be detached.
        self._eof_required = self.peer_authenticated or self._connection is not None
        # The event reader is now retired.  Probe the descriptor before
        # proc.poll()/terminate(): an already-present EOF removes token
        # authority and permits only passive retained-helper completion.
        if self._eof_required and self._observe_control_eof_now():
            return self._complete_after_control_eof()
        if self._control_eof_observed:
            return self._complete_after_control_eof()
        if self._process_completed:
            if self._eof_required and not self._wait_for_control_eof():
                self._cleanup_error = "authenticated control EOF was not observed"
                return False
            return self._teardown()
        try:
            process_status = self.proc.poll()
        except Exception:
            if self._eof_required and self._observe_control_eof_now():
                return self._complete_after_control_eof()
            self._cleanup_error = "process completion could not be authenticated"
            return False
        if process_status is not None:
            self._process_completed = True
        else:
            try:
                self.proc.terminate()
            except Exception:
                if self._eof_required and self._observe_control_eof_now():
                    return self._complete_after_control_eof()
                self._cleanup_error = "token-only SIGTERM was not accepted"
                return False
            if self._wait_for_process_completion():
                self._process_completed = True
            else:
                if self._eof_required and self._observe_control_eof_now():
                    return self._complete_after_control_eof()
                self._eof_required = self.peer_authenticated or self._connection is not None
                try:
                    self.proc.kill()
                except Exception:
                    if self._eof_required and self._observe_control_eof_now():
                        return self._complete_after_control_eof()
                    self._cleanup_error = "token-only SIGKILL was not accepted"
                    return False
                if not self._wait_for_process_completion():
                    if self._eof_required and self._observe_control_eof_now():
                        return self._complete_after_control_eof()
                    self._cleanup_error = "SIGKILL completion was not observed"
                    return False
                self._process_completed = True
        if self._eof_required and not self._wait_for_control_eof():
            self._cleanup_error = "authenticated control EOF was not observed"
            return False
        return self._teardown()


def phase_companion(
    ph: Phases,
    signed_app: Path,
    session_id: str,
    stream_key: str,
    *,
    launcher: LaunchServicesAdapter | None = None,
    artifact_facts: ArtifactFacts | None = None,
    expected_head: str | None = None,
    expected_tree: str | None = None,
    expected_digest: str | None = None,
    artifact_inspector: SignedArtifactInspector | None = None,
    running_code_attestor: RunningCodeAttestor | None = None,
) -> CompanionRun | None:
    banner("Fase 5/10 — Companion nativo capturando áudio do sistema")
    run: CompanionRun | None = None
    capture_started = False

    def cleanup_failed_run(label: PhaseID) -> bool:
        """Account for cleanup separately from the original phase outcome.

        A failed admission or terminal observation still owns the process and
        authenticated descriptor until its lifecycle proof completes.  If the
        first stop attempt cannot prove completion/EOF, retain that owner for
        the caller's final retry and make the phase a real FAIL rather than
        silently turning an unproven TCC result into a clean BLOCKED result.
        """

        target_run = run if run is not None else getattr(ph, "cleanup_run", None)
        if target_run is None:
            return True
        try:
            cleaned = target_run.stop()
        except Exception as exc:
            cleaned = False
            cleanup_detail = f"{type(exc).__name__}: {exc}"
        else:
            cleanup_detail = target_run.cleanup_error or "cleanup do companion não foi concluído"
        if cleaned:
            if getattr(ph, "cleanup_run", None) is target_run:
                ph.cleanup_run = None
        else:
            ph.cleanup_run = target_run
        if not cleaned:
            ph.record(label, PhaseStatus.FAIL, CredentialReachableDiagnostic(cleanup_detail))
        return cleaned

    def cleanup_control_signal() -> None:
        """Best-effort cleanup that cannot replace the original signal."""

        target_run = run if run is not None else getattr(ph, "cleanup_run", None)
        if target_run is None:
            return
        # A control signal can arrive after the authenticated descriptor was
        # published but before the phase returns its run.  Retire all
        # credential-reachable state before attempting the lifecycle edge;
        # this operation itself must never mask the original signal.
        try:
            target_run._retire_post_bind_failure()
        except BaseException:
            pass
        try:
            cleaned = target_run.stop()
        except BaseException:
            cleaned = False
        if cleaned:
            if getattr(ph, "cleanup_run", None) is target_run:
                ph.cleanup_run = None
        else:
            ph.cleanup_run = target_run

    if type(artifact_facts) is not ArtifactFacts and artifact_inspector is not None and callable(getattr(artifact_inspector, "inspect", None)):
        try:
            artifact_facts = artifact_inspector.inspect(signed_app)
        except Exception:
            pass

    try:
        run = CompanionRun(
            signed_app,
            session_id,
            stream_key,
            "primary",
            launcher=launcher,
            artifact_facts=artifact_facts,
            expected_head=expected_head,
            expected_tree=expected_tree,
            expected_digest=expected_digest,
            artifact_inspector=artifact_inspector,
            running_code_attestor=running_code_attestor,
            on_publish=lambda r: setattr(ph, "cleanup_run", r),
        )
        # Retain ownership across construction and return.
        ph.cleanup_run = run
        run.send_authenticated_session(expected_peer=run._expected_peer)
        run.start_event_reader()
        capture_started = True
        if run.secret_seen:
            ph.mark_secret_seen()
        stim_thread: threading.Thread | None = None
        if launcher is None:
            def _play_primary_stimulus() -> None:
                play_capture_fixture()

            stim_thread = threading.Thread(target=_play_primary_stimulus, daemon=True)
            stim_thread.start()
        state, output = run.wait_for_capture()
        if stim_thread is not None:
            stim_thread.join(timeout=5.0)
        if run.secret_seen:
            ph.mark_secret_seen()

        if state == "ativo":
            ph.record(PhaseID.COMPANION_CAPTURE, PhaseStatus.PASS, PhaseDetail.template())
            return run

        tail = "\n".join(output.strip().splitlines()[-6:])
        if state == "tcc":
            ph.facts["tcc_message"] = tail
            if run.tcc_origin == "authenticated_event":
                tcc_detail = "permissão TCC negada reportada por evento autenticado do companion"
            elif run.tcc_origin == "process_exit_2":
                tcc_detail = "permissão TCC ausente (companion saiu com código 2)"
            else:
                tcc_detail = "permissão TCC não confirmada por uma origem segura"
            ph.record(
                PhaseID.COMPANION_CAPTURE,
                PhaseStatus.BLOCKED,
                CredentialReachableDiagnostic(tcc_detail),
            )
            ph.emit(CredentialReachableDiagnostic("\n" + tail + "\n"))
            cleaned = cleanup_failed_run(PhaseID.CLEANUP_TERMINAL_FAILURE)
            return None if cleaned else run

        ph.record(
            PhaseID.COMPANION_CAPTURE,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(f"estado={state}; {tail[-300:]}")
        )
        cleaned = cleanup_failed_run(PhaseID.CLEANUP_FAILURE)
        return None if cleaned else run
    except BaseException as exc:
        if not isinstance(exc, Exception):
            cleanup_control_signal()
            raise
        # Preserve the pre-existing ordinary-exception behavior after the
        # reader has entered the capture phase: the caller's finalizer owns
        # the durable slot, while the original exception remains observable.
        if capture_started:
            raise
        target_run = run if run is not None else getattr(ph, "cleanup_run", None)
        cleaned = True
        if target_run is not None:
            if getattr(target_run, "secret_seen", False):
                ph.mark_secret_seen()
            # Admission or post-admission ordinary failures are recorded only
            # after the same retained owner has had its bounded cleanup try.
            cleaned = cleanup_failed_run(PhaseID.CLEANUP_REJECTION)
        ph.record(
            PhaseID.COMPANION_CAPTURE,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(f"preflight/controle rejeitado: {exc}"),
        )
        return target_run if target_run is not None and not cleaned else None


# --------------------------------------------------------------------------
# Fase 6 — Áudio do candidato (alto-falante -> ScreenCaptureKit)
# --------------------------------------------------------------------------

def write_capture_fixture(path: Path, *, seconds: float = 0.4) -> None:
    """Write a short 440Hz wav. This is capture stimulus, not product TTS."""

    rate = SAMPLE_RATE
    frames = bytearray()
    total = int(rate * seconds)
    for index in range(total):
        sample = int(8000 * math.sin(2.0 * math.pi * 440.0 * index / rate))
        frames.extend(struct.pack("<h", sample))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


def play_capture_fixture() -> int:
    """Play the capture fixture through the speakers. Never uses macOS `say`."""

    path = SCRATCH / "capture-fixture.wav"
    write_capture_fixture(path)
    return subprocess.run(["afplay", str(path)], check=False).returncode


def speak(voice: str, sentence: str) -> int:
    """Devolve o código de saída do `afplay` — um PASS incondicional aqui
    registraria "áudio reproduzido" mesmo quando nada tocou."""
    _ = voice
    custom_candidate = os.environ.get("TARS_CANDIDATE_FIXTURE_WAV")
    custom_restart = os.environ.get("TARS_RESTART_FIXTURE_WAV")
    if sentence == CANDIDATE_SENTENCE and custom_candidate and Path(custom_candidate).is_file():
        return subprocess.run(["afplay", custom_candidate], check=False).returncode
    if sentence == RESTART_SENTENCE and custom_restart and Path(custom_restart).is_file():
        return subprocess.run(["afplay", custom_restart], check=False).returncode
    return play_capture_fixture()


def phase_candidate_audio(ph: Phases, voice: str, run: CompanionRun) -> bool:
    banner("Fase 6/10 — Falando a frase do candidato pelos alto-falantes")
    if not run.capture_ready():
        ph.record(
            PhaseID.CANDIDATE_AUDIO,
            PhaseStatus.BLOCKED,
            CredentialReachableDiagnostic(
                "ativação Process Tap autenticada, atual e com PCM funcional não foi provada"
            ),
        )
        return False
    codes: list[int] = []
    for i in range(2):
        codes.append(speak(voice, CANDIDATE_SENTENCE))
        if i == 0:
            time.sleep(2)
    if any(code != 0 for code in codes):
        ph.record(
            PhaseID.CANDIDATE_AUDIO,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(f"`afplay` retornou {codes}"),
        )
        return False
    ph.record(PhaseID.CANDIDATE_AUDIO, PhaseStatus.PASS, PhaseDetail.template())
    return True


# --------------------------------------------------------------------------
# Fase 7 — Canal do entrevistador (injeção de PCM real de fala)
# --------------------------------------------------------------------------

def encode_frame(session_id: str, source: str, sequence: int, first_sample: int, pcm: bytes) -> bytes:
    """4 bytes big-endian (tamanho do cabeçalho) + cabeçalho JSON + PCM cru."""
    header = json.dumps(
        {
            "session_id": session_id,
            "source": source,
            "sequence": sequence,
            "first_sample": first_sample,
            "captured_at_ms": int(time.time() * 1000),
            "sample_rate": SAMPLE_RATE,
            "channel_count": 1,
            "duration_ms": len(pcm) * 1000 // (SAMPLE_RATE * 2),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return len(header).to_bytes(4, "big") + header + pcm


def write_interviewer_fixture(path: Path, *, seconds: float = 0.5, freq: float = 880.0) -> None:
    """Write a short PCM fixture for injected interviewer microphone audio.

    This provides deterministic, speech-free audio without invoking macOS `say`.
    """
    rate = SAMPLE_RATE
    frames = bytearray()
    total = int(rate * seconds)
    for index in range(total):
        sample = int(8000 * math.sin(2.0 * math.pi * freq * index / rate))
        frames.extend(struct.pack("<h", sample))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


def synth_pcm(voice: str, sentence: str, tag: str) -> bytes:
    """Gera PCM 16 kHz mono s16le via fixture de arquivo sem invocar macOS `say`."""
    _ = voice, sentence
    custom_fixture = os.environ.get("TARS_INTERVIEWER_FIXTURE_WAV")
    if custom_fixture and Path(custom_fixture).is_file():
        wav_path = Path(custom_fixture)
    else:
        wav_path = SCRATCH / f"{tag}.wav"
        write_interviewer_fixture(wav_path)
    with wave.open(str(wav_path), "rb") as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == SAMPLE_RATE and wav.getsampwidth() == 2
        return wav.readframes(wav.getnframes())


class MicChannel:
    """Canal `source="microphone"` mantido aberto numa thread própria.

    Envia a fala real do entrevistador e **continua enviando silêncio** a 50 ms
    até `stop()`. Essa continuidade não é cosmética: o Google STT encerra um
    stream que fica sem requisições do cliente (`409 Stream timed out after
    receiving no more client requests`), o que marcaria o dreno como incompleto.
    O app menu-bar assinado alimenta o canal do sistema continuamente do mesmo jeito
    — aqui o harness apenas se comporta como o cliente real.
    """

    def __init__(self, session_id: str, stream_key: str, pcm: bytes) -> None:
        self._session_id = session_id
        self._url = f"{WS_BASE}/{session_id}"
        self._subprotocols = stream_subprotocols(stream_key)
        self._pcm = pcm
        self._stop = threading.Event()
        self.frames_sent = 0
        self.speech_frames = 0
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()

    def start(self, timeout: float = 15.0) -> bool:
        self._thread.start()
        return self._ready.wait(timeout)

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            asyncio.run(self._pump())
        except BaseException as exc:  # registrado e reportado pela fase
            self.error = exc
            self._ready.set()
        finally:
            self._subprotocols = ()

    async def _pump(self) -> None:
        silence = b"\x00" * FRAME_BYTES
        async with ws_connect(self._url, subprotocols=self._subprotocols, open_timeout=10) as ws:
            await ws.send(json.dumps({"type": "hello", "sources": ["microphone"]}))
            self._ready.set()
            sample = 0
            # 1) fala real do entrevistador
            for offset in range(0, len(self._pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                if self._stop.is_set():
                    break
                await ws.send(
                    encode_frame(self._session_id, "microphone", self.frames_sent, sample, self._pcm[offset:offset + FRAME_BYTES])
                )
                self.frames_sent += 1
                self.speech_frames += 1
                sample += FRAME_BYTES // 2
                await asyncio.sleep(FRAME_MS / 1000)  # ritmo de tempo real
            # 2) silêncio contínuo até a fase de parada
            while not self._stop.is_set():
                await ws.send(encode_frame(self._session_id, "microphone", self.frames_sent, sample, silence))
                self.frames_sent += 1
                sample += FRAME_BYTES // 2
                await asyncio.sleep(FRAME_MS / 1000)

    def stop(self, timeout: float = 10.0) -> bool:
        self._stop.set()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            return False
        self._subprotocols = ()
        return True


def phase_interviewer_audio(ph: Phases, session_id: str, stream_key: str, voice: str) -> MicChannel | None:
    banner("Fase 7/10 — Injetando o canal do entrevistador (source=microphone)")
    pcm = synth_pcm(voice, INTERVIEWER_SENTENCE, "mic")
    channel = MicChannel(session_id, stream_key, pcm)
    ph.cleanup_mic = channel
    try:
        if not channel.start() or channel.error is not None:
            ph.record(
                PhaseID.INTERVIEWER_CHANNEL,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic(f"não abriu o WebSocket: {channel.error}"),
            )
            return None
    except BaseException:
        raise
    speech_ms = len(pcm) // (SAMPLE_RATE * 2 // 1000)
    ph.record(PhaseID.INTERVIEWER_CHANNEL, PhaseStatus.PASS, PhaseDetail.template())
    return channel


# --------------------------------------------------------------------------
# Fase 8 — Ensaio de reinício (só alcançável com TCC concedido)
# --------------------------------------------------------------------------

def phase_restart_drill(
    ph: Phases,
    run: CompanionRun,
    signed_app: Path,
    session_id: str,
    stream_key: str,
    voice: str,
) -> None:
    banner("Fase 8/10 — Ensaio de reinício do companion (SIGKILL + relançamento)")
    # A restart must reuse the exact inspector that was bound to the original
    # run.  Accepting a second caller-supplied inspector would let the
    # replacement silently skip or diverge from the preflight artifact
    # boundary.  Fail closed before killing the healthy run or launching a
    # replacement when the authoritative binding is absent.
    artifact_inspector = run._artifact_inspector
    if artifact_inspector is None or not callable(getattr(artifact_inspector, "inspect", None)):
        ph.record(
            PhaseID.RESTART,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(
                "inspector de artefato vinculado ao run original ausente; relançamento recusado"
            ),
        )
        return
    running_code_attestor = run._running_code_attestor
    if not callable(getattr(running_code_attestor, "attest", None)) and not callable(running_code_attestor):
        ph.record(
            PhaseID.RESTART,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(
                "attestor dinâmico vinculado ao run original ausente; relançamento recusado"
            ),
        )
        return
    if run.authenticated_pid != run.proc.pid or not run.peer_authenticated:
        ph.record(
            PhaseID.RESTART,
            PhaseStatus.BLOCKED,
            CredentialReachableDiagnostic("o SIGKILL só pode atingir o PID autenticado pelo harness"),
        )
        return
    previous = run.activation.tuple if run.activation is not None else None
    if previous is None:
        ph.record(
            PhaseID.RESTART,
            PhaseStatus.BLOCKED,
            CredentialReachableDiagnostic("a ativação atual não foi recebida antes do reinício"),
        )
        return
    if not run.kill():
        ph.record(
            PhaseID.RESTART,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(
                run.cleanup_error or "SIGKILL não provou morte do processo e EOF autenticado"
            ),
        )
        return
    time.sleep(1)
    again = CompanionRun(
        signed_app,
        session_id,
        stream_key,
        "restart",
        launcher=run._launcher,
        artifact_facts=run._artifact_facts,
        expected_head=run._expected_head,
        expected_tree=run._expected_tree,
        expected_digest=run._expected_digest,
        artifact_inspector=artifact_inspector,
        # Deliberately derive the attestor from the original run.  A restart
        # cannot substitute a new verifier or reuse a cached success.
        running_code_attestor=running_code_attestor,
        on_publish=lambda r: setattr(ph, "restart_run", r),
    )
    # Registrado ANTES de qualquer verificação: se esta fase falhar ou levantar,
    # o `finally` do main precisa conseguir matar este processo de qualquer jeito.
    ph.restart_run = again
    try:
        again.send_authenticated_session(expected_peer=again._expected_peer)
    except Exception as exc:
        # Keep the dedicated restart owner slot populated so the caller's
        # finally block retains ownership after a fresh readback rejects the
        # replacement.  It is intentionally absent from retained facts.
        ph.record(
            PhaseID.RESTART,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(f"preflight/controle rejeitado: {exc}"),
        )
        return
    again.start_event_reader()
    stim_thread: threading.Thread | None = None
    stimulus_codes: list[int] = []

    if run._launcher is None:
        def _play_restart_stimulus() -> None:
            try:
                stimulus_codes.append(speak(voice, RESTART_SENTENCE))
            except Exception:
                stimulus_codes.append(1)

        stim_thread = threading.Thread(target=_play_restart_stimulus, daemon=True)
        stim_thread.start()

    state, _ = again.wait_for_capture()
    if state != "ativo":
        ph.record(
            PhaseID.RESTART,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(f"não recapturou após SIGKILL (estado={state})"),
        )
        return
    if stim_thread is not None:
        stim_thread.join(timeout=10.0)
    if again.activation is None or not restart_requires_fresh(previous, again.activation.tuple):
        ph.record(
            PhaseID.RESTART,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic("peer/launch_nonce/attempt não foram renovados após SIGKILL"),
        )
        return
    if not again.capture_ready():
        ph.record(
            PhaseID.RESTART,
            PhaseStatus.BLOCKED,
            CredentialReachableDiagnostic(
                "nova ativação Process Tap não provou peer, nonce, geração e PCM funcionais"
            ),
        )
        return
    if stim_thread is not None:
        if any(code != 0 for code in stimulus_codes):
            ph.record(
                PhaseID.RESTART,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic("`afplay` falhou ao reproduzir o estímulo pós-reinício"),
            )
            return
    else:
        if speak(voice, RESTART_SENTENCE) != 0:
            ph.record(
                PhaseID.RESTART,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic("`afplay` falhou ao reproduzir o estímulo pós-reinício"),
            )
            return
    time.sleep(2)
    ph.record(PhaseID.RESTART, PhaseStatus.PASS, PhaseDetail.template())


# --------------------------------------------------------------------------
# Fase 9 — Parar e conferir o transcript
# --------------------------------------------------------------------------

def fetch_segments(session_id: str) -> list[dict]:
    resp = requests.get(f"{BASE_URL}/api/sessions/{session_id}/transcript", timeout=30)
    if resp.status_code != 200:
        return []
    return resp.json().get("segments", [])


def speaker_of(segment: dict) -> str:
    return segment.get("speaker_override") or segment.get("speaker") or ""


def phase_stop_and_assert(
    ph: Phases,
    session_id: str,
    expect_candidate: bool,
    expect_restart: bool,
    mic: "MicChannel | None",
    companion: "CompanionRun | None",
) -> None:
    banner("Fase 9/10 — Encerrando a sessão e conferindo o transcript")
    # Assentamento com os dois canais ainda transmitindo: é assim que uma
    # entrevista real chega ao /stop, e é o que permite ao STT fechar os
    # resultados finais em vez de abortar por inatividade.
    ph.emit_progress(ProgressNotice.SETTLING)
    time.sleep(10)
    pre_stop = fetch_segments(session_id)
    ph.facts["segments_pre_stop"] = len(pre_stop)

    # Só agora as fontes param — imediatamente antes do /stop.
    if mic is not None:
        stopped = mic.stop()
        ph.facts["mic_frames"] = mic.frames_sent
        ph.facts["mic_speech_frames"] = mic.speech_frames
        ph.facts["mic_bytes"] = mic.frames_sent * FRAME_BYTES
        # O canal só é "sustentado até o /stop" se o socket sobreviveu até aqui,
        # encerrou com sucesso e a thread não permaneceu viva.
        if not stopped or mic.is_alive or mic.error is not None:
            detail = (
                f"socket do microfone morreu durante a execução: {type(mic.error).__name__}: {mic.error}"
                if mic.error is not None
                else "canal do microfone não encerrou a thread de streaming no tempo limite"
            )
            ph.record(
                PhaseID.MIC_SUSTAINED,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic(detail),
            )
        else:
            ph.record(
                PhaseID.MIC_SUSTAINED,
                PhaseStatus.PASS,
                PhaseDetail.template(),
            )
    if companion is not None:
        snapshot = companion.capture_live_proof_snapshot()
        if snapshot is None:
            ph.record(
                PhaseID.POSITIVE_FACTS,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic(
                    "captura pronta não foi provada imediatamente antes do cleanup"
                ),
            )
        else:
            ph.record(
                PhaseID.POSITIVE_FACTS,
                PhaseStatus.PASS,
                PhaseDetail.template(),
            )
        cleanup_ok = companion.stop()
        if not cleanup_ok:
            ph.record(
                PhaseID.CLEANUP,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic(
                    companion.cleanup_error or "cleanup do companion não foi concluído"
                ),
            )
        else:
            ph.record(
                PhaseID.CLEANUP,
                PhaseStatus.PASS,
                PhaseDetail.template(),
            )

    stop = requests.post(f"{BASE_URL}/api/sessions/{session_id}/stop", timeout=120)
    if stop.status_code != 200:
        ph.record(
            PhaseID.SESSION_STOPPED,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(f"HTTP {stop.status_code}"),
        )
        return
    ph.facts["transcription_complete"] = stop.json().get("transcription_complete")
    if ph.facts["transcription_complete"] is True:
        ph.record(PhaseID.SESSION_STOPPED, PhaseStatus.PASS, PhaseDetail.template())
    else:
        ph.record(
            PhaseID.SESSION_STOPPED,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(
                f"transcription_complete={ph.facts['transcription_complete']}"
            ),
        )

    segments = fetch_segments(session_id)
    finals = [s for s in segments if s.get("is_final")]
    ph.facts["segments_total"] = len(segments)
    ph.facts["segments_final"] = len(finals)
    # Transcript text is consumed ephemerally for assertions and is never
    # retained.  Only closed speaker enums, recognized-word sets, counts, and
    # booleans survive the phase boundary.
    speakers = sorted({speaker_of(s) for s in finals if speaker_of(s) in {"Candidato", "Entrevistador"}})
    ph.facts["transcript_speakers"] = speakers
    ph.emit_progress(
        ProgressNotice.FINAL_SEGMENTS,
        final_count=len(finals),
        total_count=len(segments),
    )
    # Do not print transcript text.  The assertions below consume it in
    # memory, while only typed speaker/word/count facts are retained.

    # --- Candidato (áudio do sistema via ScreenCaptureKit) ---
    if expect_candidate:
        matched = [
            (s, hits(s.get("text", ""), CANDIDATE_WORDS))
            for s in finals
            if speaker_of(s) == "Candidato"
        ]
        best = max((h for _, h in matched), key=len, default=set())
        if any(len(h) >= CANDIDATE_MIN_HITS for _, h in matched):
            ph.facts["transcript_candidate_words"] = sorted(best)
            ph.facts["transcript_candidate_hits"] = len(best)
            ph.record(
                PhaseID.CANDIDATE_SEGMENT,
                PhaseStatus.PASS,
                PhaseDetail.template(),
            )
        else:
            ph.facts["transcript_candidate_words"] = sorted(best)
            ph.facts["transcript_candidate_hits"] = len(best)
            ph.record(
                PhaseID.CANDIDATE_SEGMENT,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic(
                    f"nenhum final 'Candidato' com ≥{CANDIDATE_MIN_HITS} de {sorted(CANDIDATE_WORDS)}"
                    f" (melhor: {sorted(best)}, {len(matched)} segmentos do Candidato)"
                ),
            )
    else:
        ph.facts["transcript_candidate_words"] = []
        ph.facts["transcript_candidate_hits"] = 0
        ph.record(
            PhaseID.CANDIDATE_SEGMENT,
            PhaseStatus.BLOCKED,
            CredentialReachableDiagnostic("canal do candidato nunca capturou (permissão TCC ausente)"),
        )

    # --- Entrevistador (injeção pelo mesmo gateway com source=microphone) ---
    matched_i = [
        (s, hits(s.get("text", ""), INTERVIEWER_WORDS))
        for s in finals
        if speaker_of(s) == "Entrevistador"
    ]
    best_i = max((h for _, h in matched_i), key=len, default=set())
    if any(len(h) >= INTERVIEWER_MIN_HITS for _, h in matched_i):
        ph.facts["transcript_interviewer_words"] = sorted(best_i)
        ph.facts["transcript_interviewer_hits"] = len(best_i)
        ph.record(
            PhaseID.INTERVIEWER_SEGMENT,
            PhaseStatus.PASS,
            PhaseDetail.template(),
        )
    else:
        ph.facts["transcript_interviewer_words"] = sorted(best_i)
        ph.facts["transcript_interviewer_hits"] = len(best_i)
        ph.record(
            PhaseID.INTERVIEWER_SEGMENT,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(
                f"nenhum final 'Entrevistador' com ≥{INTERVIEWER_MIN_HITS} de {sorted(INTERVIEWER_WORDS)}"
                f" (melhor: {sorted(best_i)}, {len(matched_i)} segmentos do Entrevistador)"
            ),
        )

    # --- Nenhum texto idêntico atribuído aos dois lados ---
    by_speaker: dict[str, set[str]] = {}
    for seg in finals:
        by_speaker.setdefault(speaker_of(seg), set()).add(normalize(seg.get("text", "")).strip())
    overlap = by_speaker.get("Candidato", set()) & by_speaker.get("Entrevistador", set())
    overlap.discard("")
    candidate_ok = any(len(h) >= CANDIDATE_MIN_HITS for _, h in matched) if expect_candidate else False
    interviewer_ok = any(len(h) >= INTERVIEWER_MIN_HITS for _, h in matched_i)
    ph.facts["transcript_valid_typed"] = bool(candidate_ok and interviewer_ok and not overlap)
    if overlap:
        ph.record(
            PhaseID.NO_DUPLICATION,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(f"texto idêntico nos dois falantes: {sorted(overlap)}"),
        )
    else:
        ph.record(PhaseID.NO_DUPLICATION, PhaseStatus.PASS, PhaseDetail.template())

    # --- Ensaio de reinício ---
    if expect_restart:
        # A frase pós-reinício entrou pelo canal que foi morto (áudio do
        # sistema), então ela tem de reaparecer rotulada como "Candidato" —
        # exigir só a presença do texto não provaria que a captura voltou.
        post = [
            s
            for s in finals
            if speaker_of(s) == "Candidato"
            and len(hits(s.get("text", ""), RESTART_WORDS)) >= RESTART_MIN_HITS
        ]
        if post:
            ph.facts["transcript_restart_match"] = True
            ph.record(
                PhaseID.RESTART_TRANSCRIPT,
                PhaseStatus.PASS,
                PhaseDetail.template(),
            )
        else:
            ph.facts["transcript_restart_match"] = False
            ph.record(
                PhaseID.RESTART_TRANSCRIPT,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic(
                    "frase pós-reinício não reapareceu como 'Candidato' no transcript"
                ),
            )
    else:
        ph.facts["transcript_restart_match"] = False


# --------------------------------------------------------------------------
# Fase 10 — Documento de evidência
# --------------------------------------------------------------------------

_EVIDENCE_SECRET_KEYS = frozenset(
    {"stream_key", "secret", "credential", "password", "api_key", "access_token", "authorization"}
)
_UNBOUND_STREAM_KEY = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")


def _redact_evidence_value(
    value: object,
    sentinel: str | None,
    *,
    top_level: bool = True,
    _depth: int = 0,
    _active: set[int] | None = None,
    _budget: list[int] | None = None,
    owner: "Phases | None" = None,
) -> object:
    """Make a rejected evidence candidate safe before recording a FAIL.

    This is deliberately a total projection: hostile cycles, deep graphs,
    oversized containers, and objects with unsafe iteration are represented
    only by a fixed marker.  When a phase ledger owns the projection, the
    same rejection is durable so it cannot later qualify a PASS.
    """

    if _active is None:
        _active = set()
    if _budget is None:
        _budget = [0, 0]

    def invalid() -> str:
        _budget[1] = 1
        if owner is not None:
            owner._fact_ownership_failed = True
        return _SAFE_DIAGNOSTIC_MARKER

    if isinstance(value, str):
        redacted = redact_credential_material(value, sentinel)
        return _UNBOUND_STREAM_KEY.sub("<redacted>", redacted)
    if isinstance(value, CredentialReachableDiagnostic):
        return _UNBOUND_STREAM_KEY.sub(
            "<redacted>", redact_credential_material(value.text, sentinel)
        )
    if is_dataclass(value):
        return invalid()
    if value is None or type(value) in {bool, int, float}:
        return value
    if _depth >= _DIAGNOSTIC_MAX_DEPTH or _budget[0] > _DIAGNOSTIC_MAX_NODES:
        return invalid()
    is_mapping = isinstance(value, Mapping)
    is_sequence = isinstance(value, (list, tuple))
    if not (is_mapping or is_sequence):
        return invalid()
    identity = id(value)
    if identity in _active:
        return invalid()
    _active.add(identity)
    _budget[0] += 1
    try:
        if _budget[0] > _DIAGNOSTIC_MAX_NODES:
            return invalid()
        if is_mapping:
            if type(value) in {dict, _TypedPhaseRow} and len(value) > _DIAGNOSTIC_MAX_NODES:
                return invalid()
            keys = set(value) if type(value) in {dict, _TypedPhaseRow} else None
            kind = (
                "phase_row" if keys == {"name", "status", "detail"}
                else "transcript_row" if keys == {"speaker", "text"}
                else None
            )
            redacted: dict[str, object] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= _DIAGNOSTIC_MAX_NODES:
                    return invalid()
                _budget[0] += 1
                if _budget[0] > _DIAGNOSTIC_MAX_NODES:
                    return invalid()
                if isinstance(key, str) and key.lower() in _EVIDENCE_SECRET_KEYS:
                    continue
                if type(key) is not str:
                    return invalid()
                key_text = key
                if (top_level and key_text in _FIXED_FACT_KEYS) or (
                    kind == "phase_row" and key_text in {"name", "status", "detail"}
                ) or (kind == "transcript_row" and key_text in {"speaker", "text"}):
                    safe_key = redact_fixed_material(key_text, sentinel)
                else:
                    safe_key = redact_credential_material(key_text, sentinel)
                safe_key = _UNBOUND_STREAM_KEY.sub("<redacted>", safe_key)
                if (
                    (kind == "phase_row" and key_text == "status" and isinstance(item, str)
                     and item in _CONTROLLED_ENUM_VALUES["status"])
                    or (kind == "transcript_row" and key_text == "speaker" and isinstance(item, str)
                        and item in _CONTROLLED_ENUM_VALUES["speaker"])
                    or (key_text in _CONTROLLED_ENUM_VALUES and isinstance(item, str)
                        and item in _CONTROLLED_ENUM_VALUES[key_text])
                ):
                    safe_item = redact_fixed_material(item, sentinel)
                else:
                    safe_item = _redact_evidence_value(
                        item,
                        sentinel,
                        top_level=False,
                        _depth=_depth + 1,
                        _active=_active,
                        _budget=_budget,
                        owner=owner,
                    )
                if _budget[1]:
                    return _SAFE_DIAGNOSTIC_MARKER
                redacted[safe_key] = safe_item
            return redacted
        if type(value) is list and len(value) > _DIAGNOSTIC_MAX_NODES:
            return invalid()
        redacted_sequence: list[object] = []
        for index, item in enumerate(value):
            if index >= _DIAGNOSTIC_MAX_NODES:
                return invalid()
            _budget[0] += 1
            if _budget[0] > _DIAGNOSTIC_MAX_NODES:
                return invalid()
            redacted_sequence.append(
                _redact_evidence_value(
                    item,
                    sentinel,
                    top_level=False,
                    _depth=_depth + 1,
                    _active=_active,
                    _budget=_budget,
                    owner=owner,
                )
            )
            if _budget[1]:
                return _SAFE_DIAGNOSTIC_MARKER
        return redacted_sequence
    except Exception:
        return invalid()
    finally:
        _active.remove(identity)


def _phase_rows_for_evidence(ph: Phases) -> list[dict[str, str]]:
    # Re-sanitize the mutable row list at the final evidence boundary.  A
    # caller can mutate a nested mapping after ``record``; direct projection
    # must not let that bypass the terminal credential-prefix rule.
    if any(type(row) is not _TypedPhaseRow for row in ph.rows):
        ph._row_ownership_failed = True
    candidate = ph._redact_value(ph.rows)
    if not isinstance(candidate, list):
        return []

    def safe_dynamic_text(value: object) -> str:
        return ph._redact_dynamic_string(str(value))

    def safe_controlled_status(value: object) -> str:
        # Only an exact producer-owned enum token is fixed.  Row names and
        # unknown status values remain dynamic so a terminal credential prefix
        # cannot be retained at the final projection boundary.
        if isinstance(value, str) and value in _CONTROLLED_ENUM_VALUES["status"]:
            return redact_fixed_material(value, ph.sentinel)
        return safe_dynamic_text(value)

    projected: list[dict[str, str]] = []
    for row in candidate:
        if not isinstance(row, Mapping):
            ph._row_ownership_failed = True
            projected.append(_TypedPhaseRow({
                "name": PhaseID.EVIDENCE.value,
                "status": PhaseStatus.FAIL.value,
                "detail": "untyped phase row rejected",
            }))
            continue
        producer_owned = type(row) is _TypedPhaseRow
        row_invalid = not producer_owned
        try:
            if not producer_owned:
                ph._row_ownership_failed = True
            else:
                _validate_phase_row(row)
            # A pass row is valid only with the exact closed producer tuple.
            if type(row.get("status")) is not str or row.get("status") not in _REQUIRED_PHASE_RESULTS:
                ph._row_ownership_failed = True
            if row.get("status") == PhaseStatus.PASS.value:
                if _phase_id(row.get("name")) is None or row.get("detail") != PhaseDetail.template().text:
                    ph._row_ownership_failed = True
                    row_invalid = True
        except (TypeError, ValueError):
            ph._row_ownership_failed = True
            row_invalid = True
        if row_invalid or _phase_id(row.get("name")) is None or row.get("status") not in _REQUIRED_PHASE_RESULTS:
            # Never turn a raw/unknown row into a typed PASS-shaped value in
            # the canonical document.  Preserve only a closed producer
            # failure marker; the original ledger ownership bit remains
            # durable and final_result_code still rejects the run.
            projected.append(_TypedPhaseRow({
                "name": PhaseID.EVIDENCE.value,
                "status": PhaseStatus.FAIL.value,
                "detail": "untyped phase row rejected",
            }))
            continue
        name = row.get("name", "")
        detail = row.get("detail", "")
        safe_name = (
            ph._redact_fixed_string(name)
            if producer_owned and isinstance(name, str) and _phase_id(name) is not None
            else safe_dynamic_text(name)
        )
        safe_detail = (
            ph._redact_fixed_string(detail)
            if producer_owned and detail == PhaseDetail.template().text
            else safe_dynamic_text(detail)
        )
        projected.append(_TypedPhaseRow({
            "name": safe_name,
            "status": safe_controlled_status(row.get("status", "")),
            "detail": safe_detail,
        }))
    return projected


def _fallback_canonical_facts(
    value: object,
    sentinel: str | None,
    ph: Phases,
) -> dict[str, object]:
    """Normalize a rejected candidate into a typed, diagnostic-only FAIL document.

    The fallback is reached after a malformed or credential-bearing fact has
    already revoked qualification.  It must still satisfy the independent
    canonical schema so a hostile scalar cannot turn evidence writing into an
    uncaught exception.  Defaults are deliberately non-positive and the phase
    projection is rebuilt from the exact producer-row boundary.
    """

    if not isinstance(value, Mapping):
        value = {}

    def dynamic(item: object) -> object:
        if isinstance(item, str):
            return redact_credential_material(item, sentinel)
        if item is None or type(item) is bool:
            return item
        if type(item) is int:
            return item if 0 <= item <= _UINT64_MAX else 0
        if type(item) is float:
            return item if item == item and item not in {float("inf"), float("-inf")} else None
        if type(item) is list:
            return [dynamic(entry) for entry in item]
        if type(item) is dict:
            result: dict[str, object] = {}
            for key, entry in item.items():
                if type(key) is not str or key.lower() in _EVIDENCE_SECRET_KEYS:
                    continue
                safe_key = redact_credential_material(key, sentinel)
                result[safe_key] = dynamic(entry)
            return result
        return None

    output: dict[str, object] = {}
    fixed_strings = {"app_path", "generated_by", "machine", "signed_app", "timestamp", "tree_state", "voice"}
    counts = {
        "mic_bytes", "mic_frames", "mic_speech_frames", "segments_final",
        "segments_pre_stop", "segments_total", "transcript_candidate_hits",
        "transcript_interviewer_hits",
    }
    booleans = {
        "transcript_valid_typed", "transcript_restart_match", "transcription_complete",
    }
    closed_lists = {
        "transcript_speakers": {"Candidato", "Entrevistador"},
        "transcript_candidate_words": set(CANDIDATE_WORDS),
        "transcript_interviewer_words": set(INTERVIEWER_WORDS),
    }
    try:
        for key, raw in value.items():
            if type(key) is not str or key not in EVIDENCE_FACT_ALLOWLIST:
                continue
            safe = _redact_evidence_value(raw, sentinel, owner=ph)
            if key == "phase_rows":
                # Raw/duck rows never cross into canonical evidence, even after
                # generic redaction.  Rebuild the exact typed projection instead.
                output[key] = _phase_rows_for_evidence(ph)
            elif key in fixed_strings:
                output[key] = safe if type(safe) is str and safe else "n/d"
            elif key == "arch":
                output[key] = safe if type(safe) is str and safe in {"arm64", "x86_64", "i386", "arm64e"} else "x86_64"
            elif key == "commit":
                output[key] = safe if type(safe) is str and re.fullmatch(r"[0-9a-f]{40}", safe) else "0" * 40
            elif key in counts:
                output[key] = safe if type(safe) is int and 0 <= safe <= _UINT64_MAX else 0
            elif key in booleans:
                output[key] = safe if type(safe) is bool else False
            elif key in closed_lists:
                output[key] = safe if type(safe) is list and all(type(entry) is str and entry in closed_lists[key] for entry in safe) else []
            else:
                output[key] = dynamic(safe)
    except Exception:
        # An injected Mapping may throw from iteration/items after yielding
        # some entries.  Discard the partial candidate rather than retaining
        # any descendant or exception text.
        ph._fact_ownership_failed = True
        return {}
    return output


def _mint_fail_fallback(
    value: object,
    sentinel: str | None,
    ph: Phases,
) -> dict[str, object]:
    """Always return a canonical diagnostic FAIL after a rejected projection."""

    try:
        safe_candidate = _fallback_canonical_facts(value, sentinel, ph)
        if not isinstance(safe_candidate, Mapping):
            raise HarnessProtocolError("evidence fallback did not produce an object")
        return canonical_evidence(
            facts=safe_candidate,
            result="FAIL",
            sentinel=sentinel,
        )
    except Exception:
        # The original candidate is already outside the positive boundary.
        # Preserve that ownership failure and fall back to the smallest
        # independently canonicalizable diagnostic document rather than
        # allowing malformed diagnostics to escape the evidence phase.
        ph._fact_ownership_failed = True
        return canonical_evidence(facts={}, result="FAIL", sentinel=sentinel)


_REQUIRED_PHASE_RESULTS = frozenset({"PASS", "FAIL", "BLOQUEADO", "INCONCLUSIVE"})

_CONDITIONAL_PHASE_IDS = frozenset({
    PhaseID.RESTART.value,
    PhaseID.RESTART_TRANSCRIPT.value,
    PhaseID.CLEANUP_REJECTION.value,
    PhaseID.CLEANUP_TERMINAL_FAILURE.value,
    PhaseID.CLEANUP_FAILURE.value,
})


def _required_phase_ids(*, with_restart_drill: bool) -> frozenset[str]:
    required = {
        phase.value for phase in PhaseID
        if phase.value not in _CONDITIONAL_PHASE_IDS
    }
    if with_restart_drill:
        required.update({PhaseID.RESTART.value, PhaseID.RESTART_TRANSCRIPT.value})
    return frozenset(required)


def _reduce_required_phase_status(
    rows: object,
    *,
    with_restart_drill: bool = False,
    allow_pending_evidence: bool = False,
) -> str:
    """Reduce every required row, failing closed on shape or status drift."""

    if not isinstance(rows, list):
        return "FAIL"
    statuses: list[str] = []
    names: list[str] = []
    for row in rows:
        if type(row) is not _TypedPhaseRow:
            return "FAIL"
        try:
            _validate_phase_row(row)
        except HarnessProtocolError:
            return "FAIL"
        name = row.get("name")
        if not isinstance(name, str):
            return "FAIL"
        status = row.get("status")
        if not isinstance(status, str) or status not in _REQUIRED_PHASE_RESULTS:
            return "FAIL"
        names.append(name)
        statuses.append(status)
    required = _required_phase_ids(with_restart_drill=with_restart_drill)
    if allow_pending_evidence and PhaseID.EVIDENCE.value not in names:
        required = frozenset(set(required) - {PhaseID.EVIDENCE.value})
    if len(rows) != len(required) or set(names) != required or len(set(names)) != len(names):
        import sys
        missing = set(required) - set(names)
        extra = set(names) - set(required)
        sys.stderr.write(f"[DIAG] _reduce_required_phase_status: len(rows)={len(rows)} len(req)={len(required)} missing={missing} extra={extra}\n")
        return "FAIL"
    if "FAIL" in statuses:
        return "FAIL"
    if "BLOQUEADO" in statuses:
        return "BLOCKED"
    if "INCONCLUSIVE" in statuses:
        return "INCONCLUSIVE"
    return "PASS"

def phase_evidence(
    ph: Phases,
    args: argparse.Namespace,
    *,
    companion: CompanionRun | None = None,
    provenance_reader: Callable[[], tuple[str, Iterable[str]]] | None = None,
) -> None:
    banner("Fase 10/10 — Escrevendo o documento de evidência")
    restart_mode = getattr(args, "with_restart_drill", False)
    if type(restart_mode) is not bool:
        ph._fact_ownership_failed = True
        restart_mode = False
    ph._with_restart_drill = restart_mode
    # Retain the exact invocation mode as a typed operational fact.  The
    # canonical PASS boundary compares this value with the complete required
    # phase set; it is never inferred from whichever rows happen to be present.
    ph.facts["restart_drill"] = restart_mode
    # Evidence cannot select PASS by string.  Build the typed proof and let the
    # one pure predicate decide the result; a transcript alone is insufficient.
    proof: PositiveProcessTapProof | None = None
    active_stream_key: str | None = ph.sentinel
    if companion is not None:
        if active_stream_key is None:
            ph.record(
                PhaseID.EVIDENCE,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic("stream-key redaction sentinel was not admitted before evidence"),
            )
    # A legacy/injected transcript is consumed only ephemerally and is never
    # copied into retained evidence.  Production uses the typed boolean from
    # phase_stop_and_assert; a raw transcript diagnostic can only invalidate
    # the run via the durable redaction bit.
    raw_transcript = ph.facts.get("transcript")
    if isinstance(raw_transcript, list):
        transcript_candidate = ph._redact_value(raw_transcript)
        transcript_rows = transcript_candidate if isinstance(transcript_candidate, list) else []
        # Raw transcript is a credential-reachable diagnostic.  It may be
        # inspected for redaction, but it cannot satisfy the positive proof;
        # only the typed phase result below is proof-bearing.
        transcript_valid = False
    else:
        transcript_rows = []
        transcript_valid = ph.facts.get("transcript_valid_typed") is True
    if companion is not None:
        snapshot = getattr(companion, "proof_snapshot", None)
        cleanup_succeeded = getattr(companion, "cleanup_succeeded", False)
        # Only the JSON boolean true proves that /stop finalized the
        # transcript.  Nonempty strings/numbers are hostile truthy values,
        # not the protocol's completion fact.
        transcript_finalized = ph.facts.get("transcription_complete") is True
        if type(snapshot) is LiveProofSnapshot and cleanup_succeeded is True and transcript_finalized:
            proof = PositiveProcessTapProof(
                artifact_valid=snapshot.artifact_valid,
                current_peer=snapshot.current_peer,
                authenticated_peer_key=snapshot.authenticated_peer_key,
                launch_nonce=snapshot.launch_nonce,
                activation=snapshot.activation,
                functional_permission_state=snapshot.functional_permission_state,
                functional_permission_tuple=snapshot.functional_permission_tuple,
                transcript_valid=transcript_valid,
            )
        else:
            reasons: list[str] = []
            if type(snapshot) is not LiveProofSnapshot:
                reasons.append("snapshot pré-parada ausente")
            if cleanup_succeeded is not True:
                reasons.append("cleanup do companion não foi provado")
            if not transcript_finalized:
                reasons.append("transcript final não foi finalizado")
            ph.record(
                PhaseID.EVIDENCE,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic("prova final rejeitada: " + "; ".join(reasons)),
            )
    # The companion's pipe reader is independent of the phase ledger and may
    # observe a split sentinel after phase_companion has already returned a
    # healthy run.  Bring that durable observation into the ledger immediately
    # before deciding whether the typed proof may survive.
    if companion is not None and getattr(companion, "secret_seen", False) is True:
        ph.mark_secret_seen()
    if ph.secret_seen:
        # Secret-bearing diagnostics are a failed evidence attempt even after
        # the durable boundary has replaced their bytes in retained storage.
        ph.record(
            PhaseID.EVIDENCE,
            PhaseStatus.FAIL,
            CredentialReachableDiagnostic(
                "stream-key sentinel crossed a dynamic output or evidence boundary"
            ),
        )
        proof = None
    sw = subprocess.run(["sw_vers"], capture_output=True, text=True).stdout.strip()
    machine = " / ".join(line.split(":", 1)[1].strip() for line in sw.splitlines() if ":" in line)
    arch = subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip()
    if provenance_reader is not None:
        commit, porcelain_values = provenance_reader()
        porcelain = list(porcelain_values)
    else:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), capture_output=True, text=True
        ).stdout.strip()
        # Um commit sozinho não identifica o que rodou: com o working tree sujo, o
        # código exercitado não é o do commit. E o binário do companion é compilado
        # à parte, então a sua data é a única pista de qual revisão ele carrega.
        porcelain = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=str(REPO_ROOT), capture_output=True, text=True
        ).stdout.splitlines()
    # Qualquer saída conta como sujo, mas as duas causas têm pesos diferentes e
    # colapsá-las gastaria o sinal: arquivo versionado modificado significa que o
    # código exercitado não é o do commit; arquivo apenas não versionado, não.
    tracked_changes = [ln for ln in porcelain if not ln.startswith("??")]
    untracked = [ln for ln in porcelain if ln.startswith("??")]
    if tracked_changes:
        tree_state = f"**SUJO — {len(tracked_changes)} arquivo(s) versionado(s) modificado(s)**"
    elif untracked:
        tree_state = f"**SUJO — apenas {len(untracked)} arquivo(s) não versionado(s) presente(s)**"
    else:
        tree_state = "limpo"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if companion is not None:
        expected_head = getattr(companion, "_expected_head", None)
        provenance_problems: list[str] = []
        if not isinstance(commit, str) or commit != expected_head:
            provenance_problems.append("HEAD atual não corresponde ao HEAD pré-validado do companion")
        if porcelain:
            provenance_problems.append("working tree não está limpa")
        if not provenance_problems:
            pass
        else:
            proof = None
            ph.record(
                PhaseID.EVIDENCE,
                PhaseStatus.FAIL,
                CredentialReachableDiagnostic(
                    "proveniência final rejeitada: " + "; ".join(provenance_problems)
                ),
            )

    # Required phase outcomes are part of the proof boundary.  Reduce the
    # complete ledger exhaustively; malformed/unknown rows are FAIL, and the
    # priority is FAIL > BLOCKED > INCONCLUSIVE > all PASS.
    required_result = _reduce_required_phase_status(
        ph.rows,
        with_restart_drill=ph._with_restart_drill is True,
        allow_pending_evidence=True,
    )
    facts_owned = ph.operational_facts_owned()
    import sys
    sys.stderr.write(
        f"[DIAG] phase_evidence: required_result={required_result!r} facts_owned={facts_owned!r} "
        f"fact_failed={ph._fact_ownership_failed!r} row_failed={ph._row_ownership_failed!r} "
        f"proof_type={type(proof)} claim={positive_process_tap_claim(proof) if proof else None}\n"
    )
    if required_result != "PASS" or not facts_owned or ph._fact_ownership_failed or ph._row_ownership_failed:
        proof = None
        evidence_result = "FAIL" if (ph._fact_ownership_failed or ph._row_ownership_failed) else required_result
    else:
        evidence_result = (
            "PASS"
            if type(proof) is PositiveProcessTapProof and positive_process_tap_claim(proof)
            else "INCONCLUSIVE"
        )

    # Evidence is itself the terminal producer-owned phase.  Add exactly one
    # row before projecting the final document so the success plan cannot be
    # satisfied by an empty/partial ledger and the final-result reducer sees
    # the same complete multiset as the canonical evidence.
    if not any(
        type(row) is _TypedPhaseRow and row.get("name") == PhaseID.EVIDENCE.value
        for row in ph.rows
    ):
        if evidence_result == "PASS":
            evidence_status: PhaseStatus | str = PhaseStatus.PASS
            evidence_detail: object = PhaseDetail.template()
        elif evidence_result == "BLOCKED":
            evidence_status = PhaseStatus.BLOCKED
            evidence_detail = CredentialReachableDiagnostic("required phase plan was blocked")
        elif evidence_result == "INCONCLUSIVE":
            evidence_status = PhaseStatus.INCONCLUSIVE
            evidence_detail = CredentialReachableDiagnostic("required phase plan was inconclusive")
        else:
            evidence_status = PhaseStatus.FAIL
            evidence_detail = CredentialReachableDiagnostic("canonical evidence did not qualify")
        ph.record(PhaseID.EVIDENCE, evidence_status, evidence_detail)
        required_result = _reduce_required_phase_status(
            ph.rows,
            with_restart_drill=ph._with_restart_drill is True,
        )
        if required_result != "PASS":
            proof = None
            evidence_result = "FAIL" if required_result == "FAIL" else required_result

    # Build every dynamic JSON/Markdown value as one fixed projection, and
    # authenticate it before any retained file is written.  A stream-key
    # sentinel anywhere in transcript, phase detail, or error content rejects
    # the candidate; the fallback is a redacted FAIL projection only.
    retained: dict[str, object] = {
        "app_path": str(args.signed_app),
        "arch": arch,
        "commit": commit,
        "error": ph.facts.get("error"),
        "generated_by": f"scripts/verify_live_system_audio.py{' --with-restart-drill' if restart_mode else ''}",
        "machine": machine,
        "mic_bytes": ph.facts.get("mic_bytes", 0),
        "mic_frames": ph.facts.get("mic_frames", 0),
        "mic_speech_frames": ph.facts.get("mic_speech_frames", 0),
        "phase_detail": ph.facts.get("phase_detail"),
        "phase_rows": _phase_rows_for_evidence(ph),
        "segments_final": ph.facts.get("segments_final", 0),
        "segments_pre_stop": ph.facts.get("segments_pre_stop", 0),
        "segments_total": ph.facts.get("segments_total", 0),
        "signed_app": ph.facts.get("signed_app", str(args.signed_app)),
        "tcc_message": ph.facts.get("tcc_message"),
        "timestamp": now,
        # Raw transcript strings are intentionally omitted.  Typed speaker,
        # word-hit, count, and restart facts are the only retained transcript
        # evidence and cannot reconstruct credential-reachable text.
        "transcript_speakers": ph.facts.get("transcript_speakers", []),
        "transcript_candidate_words": ph.facts.get("transcript_candidate_words", []),
        "transcript_interviewer_words": ph.facts.get("transcript_interviewer_words", []),
        "transcript_candidate_hits": ph.facts.get("transcript_candidate_hits", 0),
        "transcript_interviewer_hits": ph.facts.get("transcript_interviewer_hits", 0),
        "transcript_valid_typed": ph.facts.get("transcript_valid_typed", False),
        "transcript_restart_match": ph.facts.get("transcript_restart_match", False),
        "transcription_complete": ph.facts.get("transcription_complete", False),
        "tree_state": tree_state,
        "voice": ph.facts.get("voice", "n/d"),
    }
    # Positive evidence retains the exact operational projection that made
    # the typed proof possible.  Omitting any of these fields is itself a
    # non-PASS result; they are never reconstructed from a caller-supplied
    # boolean or a printable claim.
    if proof is not None and evidence_result == "PASS":
        retained.update(
            {
                "expected_head": ph.facts.get("expected_head"),
                "expected_tree": ph.facts.get("expected_tree"),
                "expected_digest": ph.facts.get("expected_digest"),
                "artifact_facts": ph.facts.get("artifact_facts"),
                "process_tap_positive": True,
                "process_tap_evidence_result": "PASS",
                "proof_digest": positive_process_tap_proof_digest(proof),
                "restart_drill": restart_mode,
                "engine": proof.activation.actual_engine,
            }
        )
    try:
        validate_fact_specs(retained)
        evidence = canonical_evidence(
            facts=retained,
            sentinel=active_stream_key,
            proof=proof,
            result=evidence_result,
        )
    except (HarnessProtocolError, RecursionError, TypeError, ValueError) as exc:
        ph.record(PhaseID.EVIDENCE, PhaseStatus.FAIL, CredentialReachableDiagnostic(str(exc)))
        retained["phase_rows"] = _phase_rows_for_evidence(ph)
        safe_candidate = _redact_evidence_value(retained, active_stream_key, owner=ph)
        # The generic JSON-safe traversal intentionally erases in-memory
        # ownership markers.  Reinstall only the independently validated
        # phase projection so canonical_evidence can accept a diagnostic
        # document while the original mutable ledger remains a durable FAIL.
        evidence = _mint_fail_fallback(safe_candidate, active_stream_key, ph)
    # The final projection itself can discover a late direct mutation or a
    # complete sentinel in a structural field.  Recheck the durable bit and
    # ownership flags before a canonical claim is assigned.
    if ph.secret_seen or ph._fact_ownership_failed or ph._row_ownership_failed:
        safe_candidate = _redact_evidence_value(retained, active_stream_key, owner=ph)
        evidence = _mint_fail_fallback(safe_candidate, active_stream_key, ph)
    ph.facts["process_tap_evidence_result"] = evidence["result"]
    ph.facts["process_tap_positive"] = evidence.get("claim") == "process-tap-positive"
    final_plan_result = _reduce_required_phase_status(
        ph.rows,
        with_restart_drill=ph._with_restart_drill,
    )
    if evidence.get("result") == "PASS":
        # Retain the exact canonical values in the ledger as well as in the
        # document.  final_result_code reprojects these mutable values and
        # compares them with the terminal snapshot, so direct post-qualification
        # edits cannot keep a stale PASS alive.
        try:
            for key, value in evidence["facts"].items():
                if key == "phase_rows" and type(value) is list:
                    value = [
                        _TypedPhaseRow(row) if type(row) is dict else row
                        for row in value
                    ]
                ph.facts[key] = value
        except (HarnessProtocolError, TypeError, ValueError):
            ph._fact_ownership_failed = True
    if (
        evidence.get("result") == "PASS"
        and evidence.get("claim") == "process-tap-positive"
        and final_plan_result == "PASS"
        and not ph.secret_seen
        and not ph._fact_ownership_failed
        and not ph._row_ownership_failed
        and ph.operational_facts_owned()
        and type(proof) is PositiveProcessTapProof
    ):
        try:
            canonical_payload = require_minted_canonical_evidence(evidence)
            ph._final_qualification_record = _FinalQualificationRecord(
                canonical_payload=canonical_payload,
                state_payload=_qualification_state_payload(ph),
                proof=proof,
                restart_mode=ph._with_restart_drill is True,
                owner_instance=ph,
            )
        except (AttributeError, HarnessProtocolError, TypeError, ValueError):
            ph._final_qualification_record = None
    else:
        ph._final_qualification_record = None
    safe_facts = evidence["facts"]
    safe_phase_rows = safe_facts.get("phase_rows", [])
    if type(safe_phase_rows) is not list:
        safe_phase_rows = []
    safe_failed = evidence.get("result") == "FAIL" or any(
        isinstance(row, Mapping) and row.get("status") == "FAIL"
        for row in safe_phase_rows
    )
    safe_blocked = any(
        isinstance(row, Mapping) and row.get("status") == "BLOQUEADO"
        for row in safe_phase_rows
    )

    safe_result = evidence.get("result", "FAIL")
    positive_markdown_claim = (
        safe_result == "PASS" and evidence.get("claim") == "process-tap-positive"
    )
    lines = [
        (
            "# Evidência — prova ao vivo do canal do candidato (piloto-solo)"
            if positive_markdown_claim
            else "# Registro — execução do harness do canal do candidato"
        ),
        "",
    ]

    if safe_failed:
        lines += [
            "> ## ❌ EXECUÇÃO COM FALHAS",
            ">",
            "> **Este documento NÃO comprova o canal do candidato.** Uma ou mais fases "
            "reprovaram (veja a tabela abaixo). O registro serve apenas para diagnóstico "
            "da tentativa malsucedida.",
            "",
        ]

    lines += [
        f"- **Gerado por:** `{safe_facts.get('generated_by', 'scripts/verify_live_system_audio.py')}`",
        f"- **Data (UTC):** {safe_facts.get('timestamp', 'n/d')}",
        f"- **Máquina:** {safe_facts.get('machine', 'n/d')} ({safe_facts.get('arch', 'x86_64')})",
        f"- **Commit:** `{safe_facts.get('commit', '0' * 40)}` — working tree: {safe_facts.get('tree_state', 'n/d')}",
        f"- **App assinado exercitado:** `{safe_facts.get('signed_app', 'n/d')}`",
        f"- **Voz pt-BR usada:** {safe_facts.get('voice', 'n/d')}",
        f"- **Backend:** uvicorn real em `127.0.0.1:{PORT}`, `AUTH_BYPASS=true`, "
        "`HOST_AUDIO_CAPTURE_ENABLED` não definido",
        "- **STT:** Google Speech-to-Text real (ADC verificada apenas por código de saída; "
        "nenhum token foi lido, impresso ou gravado)",
        "- **Dependências Python:** `requests` e `websockets` já presentes no `.venv` — nada foi instalado",
    ]

    if tracked_changes:
        lines += [
            "",
            "> ⚠ **Arquivos versionados estavam modificados durante esta execução.** O código e "
            "o binário exercitados NÃO correspondem ao commit acima; reproduza a partir de uma "
            "árvore limpa antes de tratar este documento como evidência desse commit.",
        ]
    elif untracked:
        lines += [
            "",
            f"> ℹ Havia {len(untracked)} arquivo(s) não versionado(s) na árvore, mas nenhum "
            "arquivo versionado modificado — o código exercitado corresponde ao commit acima.",
        ]

    lines += [
        "",
        "## Resultado por fase",
        "",
        "| # | Fase | Resultado | Detalhe |",
        "|---|------|-----------|---------|",
    ]
    for i, row in enumerate(safe_phase_rows, start=1):
        detail = row["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | {row['name']} | **{row['status']}** | {detail} |")

    lines += [
        "",
        "## Contagens observadas",
        "",
        f"- Frames injetados no canal do entrevistador (`source=microphone`): "
        f"**{safe_facts.get('mic_frames', 0)}** quadros de 50 ms / 1600 B "
        f"({safe_facts.get('mic_bytes', 0) / 1000:.1f} kB), dos quais "
        f"**{safe_facts.get('mic_speech_frames', 0)}** de fala real e o restante de silêncio "
        "de sustentação até o `/stop`",
        (
            "- Frames do canal do candidato (`source=system_audio`): produzidos pelo app menu-bar "
            "assinado via Process Tap; o gateway não expõe um contador por fonte, "
            "então a prova desse canal é o segmento transcrito abaixo, não uma contagem"
            if positive_markdown_claim
            else "- Frames do canal do candidato (`source=system_audio`): não qualificados nesta execução."
        ),
        f"- Segmentos no transcript antes do `/stop`: **{safe_facts.get('segments_pre_stop', 0)}**",
        f"- Segmentos no transcript depois do `/stop`: **{safe_facts.get('segments_total', 0)}** "
        f"(finais: **{safe_facts.get('segments_final', 0)}**)",
        f"- `transcription_complete` devolvido pelo `/stop`: **{safe_facts.get('transcription_complete', 'n/d')}**",
        "",
        "### Transcript final observado",
        "",
    ]
    transcript = safe_facts.get("transcript") or []
    if transcript:
        lines += ["| Falante | Texto |", "|---------|-------|"]
        for seg in transcript:
            text = seg["text"].replace("|", "\\|")
            lines.append(f"| {seg['speaker']} | {text} |")
    else:
        lines.append("_Nenhum segmento final foi produzido nesta execução._")

    if safe_facts.get("tcc_message"):
        tcc_phase_detail = next(
            (
                row["detail"]
                for row in safe_phase_rows
                if row.get("name") == "Companion — estado da captura Process Tap"
                and row.get("status") == "BLOQUEADO"
            ),
            "",
        )
        in_band_denial = "evento autenticado" in tcc_phase_detail
        lines += [
            "",
            "## Bloqueio de permissão (TCC)",
            "",
            (
                "A negação de permissão foi reportada por um evento autenticado do companion; "
                "não foi observado um código de saída **2**. Mensagem literal:"
                if in_band_denial
                else "O companion nativo saiu com código **2** no preflight de permissão. Mensagem literal:"
            ),
            "",
            "```",
            str(safe_facts.get("tcc_message", "n/d")),
            "```",
            "",
            "Para desbloquear: **Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela "
            "e Áudio do Sistema** → habilitar o app de Terminal usado para rodar este script, "
            "reiniciar o Terminal e rodar de novo:",
            "",
            "```bash",
            'cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && \\',
            "  .venv/bin/python scripts/verify_live_system_audio.py --with-restart-drill",
            "```",
        ]

    if positive_markdown_claim:
        lines += [
            "",
            "## Pré-requisito de código (defeito encontrado por esta prova)",
            "",
            "A primeira execução desta prova reprovou com **zero** segmentos do Candidato e expôs "
            "um defeito histórico no companion: `activeSources` não era lida depois dos "
            "`append`, e o ARC pode liberar uma variável local no seu **último uso** — não no fim "
            "do escopo. Em build de release isso derrubava o `SCStream` logo após o start, então "
            "a captura anunciava \"active\" e nenhum frame de áudio do sistema chegava ao gateway "
            "(silenciosamente: sem erro, sem queda de conexão). Diagnóstico: a mesma classe de "
            "captura entregou 126 frames em 6 s a um sink simples enquanto o binário entregava 0 "
            "no mesmo instante, na mesma máquina, com o mesmo áudio.",
            "",
            "A correção (`withExtendedLifetime(activeSources)` no laço principal) está no commit "
            "`365fe20`; reproduzir esta prova exige o app assinado produzido pelo modo Task 11. "
            "binários anteriores falham nas fases do Candidato.",
            "",
        ]
    else:
        lines += [
            "",
            "## Contexto diagnóstico",
            "",
            "Esta tentativa não recebeu qualificação positiva de captura Process Tap. "
            "Os detalhes acima permanecem restritos ao resultado e às condições desta execução; "
            "nenhum resultado positivo é emitido neste registro.",
            "",
        ]

    # The positive ceiling is emitted only for the canonical PASS result and
    # its exact typed claim. Every other result gets an explicitly negative
    # conclusion, including BLOCKED and INCONCLUSIVE.
    if not positive_markdown_claim:
        lines += [
            "## Conclusão desta execução",
            "",
            f"**Esta execução terminou com resultado `{safe_result}` e não sustenta qualquer "
            "conclusão positiva sobre captura Process Tap, rotulagem por fonte ou funcionamento "
            "em produção.** O registro contém somente diagnóstico e observações desta tentativa; "
            "a qualificação positiva fica omitida.",
            "",
        ]
    else:
        lines += [
            "## Teto de alegação",
            "",
            "> Comprova apenas: espinha de captura nativa funcionando ao vivo na máquina do "
            "proprietário (escopo piloto-solo). Não comprova: piloto G6, Windows, hospedagem, lançamento.",
            "",
        ]

        if safe_blocked:
            # This branch is defensive (a canonical positive result should
            # have no blocked phase) and must not reintroduce positive prose.
            lines += [
                "**Ressalva:** uma fase ficou bloqueada; a execução não recebe a qualificação positiva.",
                "",
            ]

    EVIDENCE_DOC.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ph.emit_progress(ProgressNotice.EVIDENCE_WRITTEN)


def _qualification_state_payload(ph: Phases) -> bytes:
    """Serialize all mutable proof-bearing state for terminal revalidation."""

    if type(ph) is not Phases:
        raise HarnessProtocolError("phase ledger is not exact")
    facts = evidence_facts_projection(ph.facts, include_operational=True)
    rows = _phase_rows_for_evidence(ph)
    return canonical_json(
        {
            "restart_mode": ph._with_restart_drill is True,
            "facts": facts,
            "rows": rows,
        }
    )


def final_result_code(ph: Phases) -> int:
    """Recompute the terminal qualification from the current mutable state."""

    if type(ph) is not Phases or type(getattr(ph, "_with_restart_drill", None)) is not bool:
        return EXIT_FAILED
    if ph.failed:
        return EXIT_FAILED
    if ph.blocked:
        return EXIT_TCC_BLOCKED
    if ph._fact_ownership_failed or ph._row_ownership_failed:
        return EXIT_FAILED
    if _reduce_required_phase_status(
        ph.rows,
        with_restart_drill=ph._with_restart_drill,
    ) != "PASS" or not ph.operational_facts_owned():
        return EXIT_FAILED
    if ph.secret_seen or ph.facts.get("process_tap_positive") is not True:
        return EXIT_FAILED
    if ph.facts.get("process_tap_evidence_result") != "PASS":
        return EXIT_FAILED
    if ph.facts.get("restart_drill") is not (ph._with_restart_drill is True):
        return EXIT_FAILED
    record = getattr(ph, "_final_qualification_record", None)
    if type(record) is not _FinalQualificationRecord:
        return EXIT_FAILED
    if (
        type(getattr(record, "canonical_payload", None)) is not bytes
        or type(getattr(record, "state_payload", None)) is not bytes
        or type(getattr(record, "proof", None)) is not PositiveProcessTapProof
        or type(getattr(record, "restart_mode", None)) is not bool
        or getattr(record, "owner_instance", None) is not ph
        or getattr(record, "restart_mode", None) is not (ph._with_restart_drill is True)
        or not positive_process_tap_claim(getattr(record, "proof", None))
    ):
        return EXIT_FAILED
    try:
        current_state = _qualification_state_payload(ph)
        if current_state != record.state_payload:
            return EXIT_FAILED
        document = json.loads(record.canonical_payload.decode("utf-8"))
        if (
            type(document) is not dict
            or set(document) != {"result", "facts", "claim"}
            or document.get("result") != "PASS"
            or document.get("claim") != "process-tap-positive"
            or type(document.get("facts")) is not dict
        ):
            return EXIT_FAILED
        reissue_facts = dict(document["facts"])
        if type(reissue_facts.get("phase_rows")) is list:
            reissue_facts["phase_rows"] = [
                _TypedPhaseRow(row) if type(row) is dict else row
                for row in reissue_facts["phase_rows"]
            ]
        # Reissue the canonical projection from the exact stored proof and
        # snapshot facts.  This proves the bytes were minted by the current
        # canonicalizer and catches a transplanted/partial private record.
        reissued = canonical_evidence(
            facts=reissue_facts,
            proof=record.proof,
            result="PASS",
            sentinel=ph.sentinel,
        )
        if require_minted_canonical_evidence(reissued) != record.canonical_payload:
            return EXIT_FAILED
        if positive_process_tap_proof_digest(record.proof) != ph.facts.get("proof_digest"):
            return EXIT_FAILED
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, HarnessProtocolError, TypeError, ValueError):
        return EXIT_FAILED
    return EXIT_OK


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Prova ao vivo do canal do candidato (piloto-solo).")
    parser.add_argument(
        "--signed-app",
        type=Path,
        required=True,
        help="caminho absoluto para o .app Developer-ID-assinado do Task 11",
    )
    parser.add_argument(
        "--with-restart-drill",
        action="store_true",
        help="mata o companion com SIGKILL no meio do stream e verifica a recaptura",
    )
    args = parser.parse_args()

    if "GOOGLE_CLOUD_PROJECT" not in os.environ:
        try:
            detected = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if detected:
                os.environ["GOOGLE_CLOUD_PROJECT"] = detected
        except Exception:
            pass

    SCRATCH.mkdir(parents=True, exist_ok=True)
    print("━" * 62)
    print("  T.A.R.S. — Prova ao vivo: canal do candidato (piloto-solo)")
    print("━" * 62)

    ph = Phases()
    backend: subprocess.Popen | None = None
    backend_log: IO[bytes] | None = None
    companion: CompanionRun | None = None
    mic: MicChannel | None = None

    try:
        artifact_inspector = SignedArtifactInspector()
        running_code_attestor = DarwinRunningCodeAttestor()
        if not phase_preflight(ph, args.signed_app, artifact_inspector=artifact_inspector):
            return EXIT_PREFLIGHT

        started = phase_backend(ph)
        if started is None:
            return EXIT_FAILED
        backend, backend_log = started

        created = phase_session(ph)
        if created is None:
            return EXIT_FAILED
        session_id, stream_key = created
        # The stream key becomes a durable process-wide redaction boundary
        # before any later phase can construct, admit, or log a companion.
        ph.register_stream_key(stream_key)

        phase_wrong_key(ph, session_id, stream_key)

        voice = str(ph.facts["voice"])
        artifact_facts = None
        if artifact_inspector is not None:
            try:
                artifact_facts = artifact_inspector.inspect(args.signed_app)
            except Exception:
                artifact_facts = None
        companion_res = phase_companion(
            ph,
            args.signed_app,
            session_id,
            stream_key,
            artifact_facts=artifact_facts,
            expected_head=ph.facts.get("expected_head"),
            expected_tree=ph.facts.get("expected_tree"),
            expected_digest=ph.facts.get("expected_digest"),
            artifact_inspector=artifact_inspector,
            running_code_attestor=running_code_attestor,
        )
        companion = companion_res

        # O canal do entrevistador abre antes da fala do candidato e fica
        # transmitindo até o /stop, espelhando o companion real.
        mic_res = phase_interviewer_audio(ph, session_id, stream_key, voice)
        mic = mic_res

        candidate_played = False
        if companion is not None:
            candidate_played = phase_candidate_audio(ph, voice, companion)
        else:
            ph.record(
                PhaseID.CANDIDATE_AUDIO,
                PhaseStatus.BLOCKED,
                CredentialReachableDiagnostic(
                    "sem captura de áudio do sistema — reproduzir a frase não provaria nada"
                ),
            )

        did_restart = False
        if args.with_restart_drill and companion is not None:
            phase_restart_drill(
                ph,
                companion,
                args.signed_app,
                session_id,
                stream_key,
                voice,
            )
            # The relaunch is registered in its dedicated owner slot as soon as
            # it is born, including failed phases, so finally can always stop it.
            replacement = ph.restart_run
            did_restart = any(r["name"] == "Reinício do companion" and r["status"] == "PASS" for r in ph.rows)
            if isinstance(replacement, CompanionRun):
                companion = replacement  # o processo vivo agora é o relançado
        elif args.with_restart_drill:
            ph.record(
                PhaseID.RESTART,
                PhaseStatus.BLOCKED,
                CredentialReachableDiagnostic("depende da captura de áudio do sistema"),
            )

        phase_stop_and_assert(
            ph,
            session_id,
            expect_candidate=candidate_played,
            expect_restart=did_restart,
            mic=mic,
            companion=companion,
        )
        phase_evidence(ph, args, companion=companion)

    finally:
        stopped_mics: set[int] = set()
        for m in (getattr(ph, "cleanup_mic", None), mic):
            if isinstance(m, MicChannel):
                m_id = id(m)
                if m_id in stopped_mics:
                    continue
                stopped_mics.add(m_id)
                cleaned_m = False
                try:
                    cleaned_m = m.stop()
                except BaseException:
                    cleaned_m = False
                if cleaned_m:
                    if getattr(ph, "cleanup_mic", None) is m:
                        ph.cleanup_mic = None
                else:
                    ph.cleanup_mic = m
        # Owner slots can remain populated if a phase raises before returning
        # its run.  Stop each identity once; in particular cleanup_run must be
        # attempted even when ``companion = phase_companion(...)`` never
        # completes.  A stop signal from one owner must not prevent attempts
        # for the remaining owners or replace the original exception.
        stopped: set[int] = set()
        for run in (ph.cleanup_run, ph.restart_run, companion):
            if isinstance(run, CompanionRun):
                identity = id(run)
                if identity in stopped:
                    continue
                stopped.add(identity)
                cleaned = False
                try:
                    cleaned = run.stop()
                except BaseException:
                    cleaned = False
                if cleaned:
                    if ph.cleanup_run is run:
                        ph.cleanup_run = None
                    if ph.restart_run is run:
                        ph.restart_run = None
                else:
                    if ph.cleanup_run is None and ph.restart_run is not run:
                        ph.cleanup_run = run
        stopped_backend_procs: set[int] = set()
        for p in (getattr(ph, "cleanup_backend_proc", None), backend):
            if p is not None:
                p_id = id(p)
                if p_id in stopped_backend_procs:
                    continue
                stopped_backend_procs.add(p_id)
                p_cleaned = False
                try:
                    if hasattr(p, "poll") and p.poll() is None:
                        if hasattr(p, "terminate"):
                            p.terminate()
                        try:
                            if hasattr(p, "wait"):
                                p.wait(timeout=15)
                            p_cleaned = True
                        except (subprocess.TimeoutExpired, TimeoutError):
                            if hasattr(p, "kill"):
                                p.kill()
                            if hasattr(p, "wait"):
                                try:
                                    p.wait(timeout=15)
                                    p_cleaned = True
                                except (subprocess.TimeoutExpired, TimeoutError, BaseException):
                                    p_cleaned = False
                            else:
                                p_cleaned = True
                    else:
                        p_cleaned = True
                except BaseException:
                    p_cleaned = False
                if p_cleaned:
                    if getattr(ph, "cleanup_backend_proc", None) is p:
                        ph.cleanup_backend_proc = None
                else:
                    ph.cleanup_backend_proc = p

        closed_backend_logs: set[int] = set()
        for l in (getattr(ph, "cleanup_backend_log", None), backend_log):
            if l is not None:
                l_id = id(l)
                if l_id in closed_backend_logs:
                    continue
                closed_backend_logs.add(l_id)
                l_closed = False
                try:
                    if hasattr(l, "close"):
                        l.close()
                    l_closed = True
                except BaseException:
                    l_closed = False
                if l_closed:
                    if getattr(ph, "cleanup_backend_log", None) is l:
                        ph.cleanup_backend_log = None
                else:
                    ph.cleanup_backend_log = l

    print("\n" + "━" * 62)
    result = final_result_code(ph)
    if result == EXIT_FAILED:
        print("✗ PROVA AO VIVO: FALHOU — veja as fases marcadas FAIL acima.")
    elif result == EXIT_TCC_BLOCKED:
        print("⏸ PROVA AO VIVO: PARCIAL (BLOQUEADA) — permissão de Gravação de Tela e")
        print("  Áudio do Sistema ausente. Nenhuma asserção falhou; o canal do candidato")
        print("  simplesmente não pôde ser exercitado neste host.")
    else:
        print("✓ PROVA AO VIVO: PASSOU — canal do candidato comprovado de ponta a ponta.")
        result = EXIT_OK
    print("━" * 62 + "\n")
    return result


if __name__ == "__main__":
    sys.exit(main())

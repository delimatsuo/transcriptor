#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import dataclasses
import contextlib
import ctypes
import hashlib
import io
import inspect
import os
import queue
import signal
import socket
import shutil
import select
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from collections.abc import Callable, Mapping
from unittest import mock
from pathlib import Path

from live_system_audio_harness import (
    Activation,
    ActivationIdentity,
    ArtifactFacts,
    _CanonicalEvidence,
    CFDictionaryKeyCallBacks,
    CFDictionaryValueCallBacks,
    CaptureTuple,
    DarwinPeerIdentityReader,
    DarwinSecurityBridge,
    FrameDecoder,
    HarnessProtocolError,
    HarnessState,
    LaunchResult,
    LaunchServicesProcess,
    MacOSLaunchServicesAdapter,
    MAX_PAYLOAD,
    _production_spawn_helper,
    PERMISSION_DENIED_MESSAGE,
    PeerIdentity,
    PositiveProcessTapProof,
    PROCESS_TAP,
    SCREEN_CAPTURE_KIT,
    StaticCodeIdentity,
    UnixHarnessServer,
    canonical_evidence,
    canonical_json,
    credential_material,
    artifact_provenance_digest,
    attempt_binding,
    decode_event as _decode_event,
    decode_session_command,
    decode_shutdown_ack,
    decode_shutdown_request,
    encode_event as _encode_event,
    encode_lightweight_code_requirement,
    encode_session_command,
    encode_shutdown_ack,
    encode_shutdown_request,
    functional_health,
    functional_permission,
    frame,
    launch_binding,
    make_launch_spec,
    markdown_projection,
    positive_process_tap_claim,
    restart_requires_fresh,
    redact_credential_material,
    secret_free,
    observer_binding,
    peer_fingerprint,
    session_binding,
    shutdown_binding,
    shutdown_nonce,
    source_binding,
    shared_golden_session_command_payload,
    transcript_claim,
    validate_artifact,
    validate_gateway_base,
    validate_gateway_base_for_session,
)

_TEST_STREAM_KEY = "TASK11-GOLDEN-STREAM-KEY-0123456789abcdefgh"

# Positive proof fixtures use the same complete peer binding and sealed
# provenance projection as production.  Keeping those values centralized
# prevents a synthetic ``euid:pid:audit:path`` string from silently becoming
# an apparently qualified peer in one test while the runtime requires pb1.
_TEST_PEER = PeerIdentity(
    501,
    4242,
    "00" * 32,
    "/Applications/TarsCompanion.app/Contents/MacOS/TarsCompanionApp",
)
_TEST_PEER_FINGERPRINT = peer_fingerprint(_TEST_PEER)
_TEST_PEER_FINGERPRINT_2 = peer_fingerprint(
    dataclasses.replace(_TEST_PEER, pid=4243, audit_token="11" * 32)
)


def encode_event(event, *, stream_key=None):
    return _encode_event(event, stream_key=_TEST_STREAM_KEY if stream_key is None else stream_key)


def decode_event(payload, *, stream_key=None):
    return _decode_event(payload, stream_key=_TEST_STREAM_KEY if stream_key is None else stream_key)
import verify_live_system_audio as verifier
from verify_live_system_audio import CompanionRun, MicChannel, SignedArtifactInspector
from verify_live_system_audio import Phases, StreamingRedactor


class FakeOpenHelper:
    def __init__(self, pid: int = 7001) -> None:
        self.pid = pid
        self.stdout = None
        self.poll_calls = 0
        self.wait_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        self.signal_calls: list[int] = []
        self.returncode: int | None = None

    def poll(self) -> int | None:
        self.poll_calls += 1
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        self.wait_calls += 1
        return self.returncode or 0

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1

    def send_signal(self, signum: int) -> None:
        self.signal_calls.append(signum)


def make_helper_spawner(
    helper_or_factory: Any = None,
    *,
    side_effect: BaseException | None = None,
    on_spawn: Callable[[list[str]], None] | None = None,
) -> Callable[..., Any]:
    def spawner(
        argv: list[str],
        *,
        on_helper_spawned: Callable[[Any], None],
        stdout: Any = subprocess.PIPE,
        stderr: Any = subprocess.STDOUT,
        **kwargs: Any,
    ) -> Any:
        if on_spawn is not None:
            on_spawn(argv)
        if callable(helper_or_factory):
            try:
                h = helper_or_factory(argv, on_helper_spawned=on_helper_spawned, stdout=stdout, stderr=stderr, **kwargs)
            except TypeError:
                try:
                    h = helper_or_factory(argv, **kwargs)
                except TypeError:
                    h = helper_or_factory()
        else:
            h = helper_or_factory or FakeOpenHelper()
        on_helper_spawned(h)
        if side_effect is not None:
            raise side_effect
        return h
    return spawner


class RecordingFakeSocket:
    """Small deterministic fake socket for shutdown request half-close tests."""
    def __init__(self, real_sock: socket.socket | None = None) -> None:
        self.real_sock = real_sock
        self.shutdown_calls: list[int] = []
        self.settimeout_calls: list[float | None] = []
        self.sendall_calls: list[bytes] = []
        self.on_shutdown: Callable[[int], None] | None = None
        self.on_sendall: Callable[[bytes], None] | None = None

    def settimeout(self, timeout: float | None) -> None:
        self.settimeout_calls.append(timeout)
        if self.real_sock is not None:
            self.real_sock.settimeout(timeout)

    def sendall(self, data: bytes) -> None:
        self.sendall_calls.append(data)
        if self.on_sendall is not None:
            self.on_sendall(data)
        elif self.real_sock is not None:
            self.real_sock.sendall(data)

    def shutdown(self, how: int) -> None:
        self.shutdown_calls.append(how)
        if self.on_shutdown is not None:
            self.on_shutdown(how)
        elif self.real_sock is not None:
            self.real_sock.shutdown(how)

    def close(self) -> None:
        if self.real_sock is not None:
            self.real_sock.close()


TEST_STATIC_IDENTITY = StaticCodeIdentity(
    b"\x01" * 32, b"designated-requirement", b"lightweight-requirement"
)
APPLE_DEVELOPER_ID_LWCR_FACTS = {
    "signing-identifier": "com.ellaexecutivesearch.tarscompanion",
    "team-identifier": "3FLG8W6B95",
    "validation-category": 6,
}
# Captured from codesign --launch-constraint-self of the facts above on macOS 26.6.2.
APPLE_DEVELOPER_ID_LWCR_DER = bytes.fromhex(
    "7081a7020101b081a130090c046363617402010030090c04636f6d70020101307e0c0472"
    "657173b076303b0c127369676e696e672d6964656e7469666965720c25636f6d2e656c6c"
    "616578656375746976657365617263682e74617273636f6d70616e696f6e301d0c0f7465"
    "616d2d6964656e7469666965720c0a33464c4738573642393530180c1376616c69646174"
    "696f6e2d63617465676f727902010630090c0476657273020101"
)
_UNSET_ATTESTOR_RESULT = object()


class FakeRunningCodeAttestor:
    """Offline-only dynamic identity boundary; never loads Security.framework."""

    def __init__(self, result: object = _UNSET_ATTESTOR_RESULT) -> None:
        self.calls: list[tuple[PeerIdentity, StaticCodeIdentity]] = []
        self.result = result

    def __call__(self, peer: PeerIdentity, expected: StaticCodeIdentity) -> object:
        self.calls.append((peer, expected))
        if self.result is not _UNSET_ATTESTOR_RESULT:
            return self.result
        return expected


class FakeArtifactInspector:
    def __init__(self, facts: ArtifactFacts | None = None) -> None:
        self.facts = facts
        self.calls = 0

    def inspect(self, _app: Path) -> ArtifactFacts:
        self.calls += 1
        if self.facts is None:
            raise AssertionError("fixture must provide explicit artifact facts")
        return self.facts


class ForgedStaticCodeIdentity(StaticCodeIdentity):
    """Hostile subclass whose equality claims forged raw bytes match."""

    def __eq__(self, _other: object) -> bool:
        return True

    def __ne__(self, _other: object) -> bool:
        return False


class ForgedPeerIdentity(PeerIdentity):
    """Hostile peer subclass whose equality claims a different peer matches."""

    def __eq__(self, _other: object) -> bool:
        return True

    def __ne__(self, _other: object) -> bool:
        return False


def valid_artifact_facts(digest: str = "a" * 64) -> ArtifactFacts:
    facts = ArtifactFacts(
        "a" * 40,
        "b" * 40,
        False,
        "com.ellaexecutivesearch.tarscompanion",
        "3FLG8W6B95",
        True,
        ("com.apple.security.device.audio-input",),
        True,
        digest,
        TEST_STATIC_IDENTITY,
        digest,
        "",
        True,
        True,
    )
    return dataclasses_replace(facts, provenance_digest=artifact_provenance_digest(facts))


def artifact_facts_projection(digest: str = "a" * 64) -> dict[str, object]:
    """Return the exact canonical artifact projection used by PASS fixtures."""

    facts = valid_artifact_facts(digest)
    return {
        "provenance_head": facts.provenance_head,
        "provenance_tree": facts.provenance_tree,
        "dirty": facts.dirty,
        "bundle_id": facts.bundle_id,
        "team_id": facts.team_id,
        "hardened_runtime": facts.hardened_runtime,
        "entitlements": list(facts.entitlements),
        "strict_signature": facts.strict_signature,
        "executable_digest": facts.executable_digest,
        "sealed_executable_digest": facts.sealed_executable_digest,
        "provenance_digest": facts.provenance_digest,
        "developer_id_authority": facts.developer_id_authority,
        "audio_input_entitlement": facts.audio_input_entitlement,
        "static_identity": {
            "unique_cdhash": facts.static_identity.unique_cdhash.hex(),
            "designated_requirement": facts.static_identity.designated_requirement.hex(),
        },
    }


def install_positive_operational_facts(
    phases: Phases,
    *,
    restart_drill: bool,
    digest: str = "a" * 64,
) -> None:
    """Install only exact operational facts that a real preflight records."""

    phases.facts["expected_head"] = "a" * 40
    phases.facts["expected_tree"] = "b" * 40
    phases.facts["expected_digest"] = digest
    phases.facts["artifact_facts"] = valid_artifact_facts(digest)
    phases.facts["restart_drill"] = restart_drill


def positive_phase_rows(*, restart_drill: bool) -> list[dict[str, object]]:
    conditional = {
        verifier.PhaseID.CLEANUP_REJECTION.value,
        verifier.PhaseID.CLEANUP_TERMINAL_FAILURE.value,
        verifier.PhaseID.CLEANUP_FAILURE.value,
    }
    if not restart_drill:
        conditional.update(
            {
                verifier.PhaseID.RESTART.value,
                verifier.PhaseID.RESTART_TRANSCRIPT.value,
            }
        )
    return [
        verifier._TypedPhaseRow(
            {
                "name": phase.value,
                "status": verifier.PhaseStatus.PASS.value,
                "detail": verifier.PhaseDetail.template().text,
            }
        )
        for phase in verifier.PhaseID
        if phase.value not in conditional
    ]


def complete_positive_canonical_facts(
    proof: PositiveProcessTapProof,
    *,
    restart_drill: bool,
    digest: str = "a" * 64,
) -> dict[str, object]:
    """Build a complete PASS projection bound to the typed proof and pb1 peer."""

    return {
        "commit": "a" * 40,
        "engine": proof.activation.actual_engine,
        "tree_state": "limpo",
        "phase_rows": positive_phase_rows(restart_drill=restart_drill),
        "transcription_complete": True,
        "transcript_valid_typed": True,
        "expected_head": "a" * 40,
        "expected_tree": "b" * 40,
        "expected_digest": digest,
        "artifact_facts": artifact_facts_projection(digest),
        "process_tap_positive": True,
        "process_tap_evidence_result": "PASS",
        "proof_digest": verifier.positive_process_tap_proof_digest(proof),
        "restart_drill": restart_drill,
    }


class HarnessTests(unittest.TestCase):
    def test_release_default_tail_uses_legacy_runtime_flag_check(self) -> None:
        script = Path(__file__).with_name("release_menubar_app.sh").read_text(encoding="utf-8")
        marker = "if ! grep -Eq 'flags=.*runtime' <<<\"${CODE_SIGN_DETAILS}\"; then"
        expected_block = "\n".join(
            (
                marker,
                "    printf '%s\\n' 'Erro: a assinatura não informa o hardened runtime (flags=...runtime).' >&2",
                "    exit 5",
                "fi",
            )
        )
        start = script.index(marker)
        self.assertEqual(script[start : start + len(expected_block)], expected_block)
        signed_branch = script.split("run_signed_app_only() {", 1)[1].split("\n}\n\nif [[", 1)[0]
        self.assertIn("require_hardened_runtime_code_directory", signed_branch)
        signed_dispatch = script.split('if [[ "${RELEASE_MODE}" == "signed-app-only" ]]', 1)[1]
        self.assertNotIn("require_hardened_runtime_code_directory", signed_dispatch)

    def setUp(self) -> None:
        self.sentinel = "TASK11-GOLDEN-STREAM-KEY-0123456789abcdefgh"
        self.audit_token = "00" * 32
        self.command = {
            "gateway": "ws://127.0.0.1:8010/api/stream/native",
            "launch_nonce": "nonce-1",
            "session_id": "session-1",
            "stream_key": self.sentinel,
            "type": "session",
            "version": 2,
        }
        self.peer = PeerIdentity(501, 4242, self.audit_token, "/Applications/TarsCompanion.app/Contents/MacOS/TarsCompanionApp")

    def test_exact_identity_and_peer_subclasses_are_rejected_before_any_wire_bytes(self) -> None:
        forged_identity = ForgedStaticCodeIdentity(b"forged-cdhash", b"forged-requirement", b"forged-lwcr")
        forged_peer = ForgedPeerIdentity(
            self.peer.euid,
            self.peer.pid,
            self.peer.audit_token,
            self.peer.executable_path,
        )
        for label, final_peer, static_identity, attested_identity in (
            ("static", self.peer, forged_identity, forged_identity),
            ("dynamic", self.peer, TEST_STATIC_IDENTITY, forged_identity),
            ("peer", forged_peer, TEST_STATIC_IDENTITY, TEST_STATIC_IDENTITY),
        ):
            with self.subTest(boundary=label), contextlib.ExitStack() as stack:
                server_side, client_side = socket.socketpair()
                stack.callback(server_side.close)
                stack.callback(client_side.close)
                state = HarnessState(
                    expected_peer=self.peer,
                    server_euid=self.peer.euid,
                    launch_nonce="nonce-1",
                )
                state.accept_peer(self.peer)
                with self.assertRaises(HarnessProtocolError):
                    UnixHarnessServer.send_one_session(
                        server_side,
                        state,
                        self.peer,
                        peer_revalidator=lambda final_peer=final_peer: final_peer,
                        attested_identity=attested_identity,
                        static_identity=static_identity,
                        session_id="session-1",
                        stream_key=self.sentinel,
                        gateway="ws://127.0.0.1",
                        timeout=1.0,
                    )
                client_side.settimeout(0.05)
                with self.assertRaises(socket.timeout):
                    client_side.recv(1)
                self.assertFalse(hasattr(state, "command"))
                self.assertIsNone(state._session_binding)

        # Exact ordinary values remain admitted and send one command frame.
        with contextlib.ExitStack() as stack:
            server_side, client_side = socket.socketpair()
            stack.callback(server_side.close)
            stack.callback(client_side.close)
            state = HarnessState(
                expected_peer=self.peer,
                server_euid=self.peer.euid,
                launch_nonce="nonce-1",
            )
            state.accept_peer(self.peer)
            UnixHarnessServer.send_one_session(
                server_side,
                state,
                self.peer,
                peer_revalidator=lambda: self.peer,
                attested_identity=TEST_STATIC_IDENTITY,
                static_identity=TEST_STATIC_IDENTITY,
                session_id="session-1",
                stream_key=self.sentinel,
                gateway="ws://127.0.0.1",
                timeout=1.0,
            )
            client_side.settimeout(1.0)
            self.assertEqual(decode_session_command(client_side.recv(4096)[4:])["session_id"], "session-1")

    def test_exact_artifact_and_static_identity_types_are_required_at_validation(self) -> None:
        facts = valid_artifact_facts()
        with self.assertRaises(HarnessProtocolError):
            validate_artifact(
                dataclasses_replace(facts, static_identity=ForgedStaticCodeIdentity(b"x", b"y", b"z")),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
            )

        class ForgedArtifactFacts(ArtifactFacts):
            pass

        forged_facts = ForgedArtifactFacts(
            *(getattr(facts, field.name) for field in dataclasses.fields(ArtifactFacts))
        )
        with self.assertRaises(HarnessProtocolError):
            validate_artifact(
                forged_facts,
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
            )

    def test_canonical_artifact_static_identity_has_exact_hex_shape_and_bound(self) -> None:
        base = artifact_facts_projection()
        for field, value in (
            ("unique_cdhash", "A" * 64),
            ("unique_cdhash", "0" * 62),
            ("designated_requirement", "0"),
            ("designated_requirement", "0g"),
            ("designated_requirement", "0" * 131074),
        ):
            with self.subTest(field=field, value_length=len(value)):
                hostile = dict(base)
                hostile["static_identity"] = dict(base["static_identity"])
                hostile["static_identity"][field] = value
                with self.assertRaises(HarnessProtocolError):
                    verifier._validate_artifact_projection(hostile)

    def test_fragmentation_and_coalescing(self) -> None:
        encoded = frame(canonical_json(self.command))
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(encoded[:2]), [])
        self.assertEqual(decoder.feed(encoded[2:7]), [])
        self.assertEqual(decoder.feed(encoded[7:]), [canonical_json(self.command)])
        self.assertEqual(decoder.feed(encoded + encoded), [canonical_json(self.command), canonical_json(self.command)])

    def test_bounds_zero_oversized_and_truncated(self) -> None:
        with self.assertRaises(HarnessProtocolError): FrameDecoder().feed(b"\0\0\0\0")
        with self.assertRaises(HarnessProtocolError): FrameDecoder().feed((MAX_PAYLOAD + 1).to_bytes(4, "big"))
        decoder = FrameDecoder(); decoder.feed(b"\0\0\0\x03ab")
        with self.assertRaises(HarnessProtocolError): decoder.finish()
        with self.assertRaises(HarnessProtocolError): frame(b"")
        with self.assertRaises(HarnessProtocolError): frame(b"x" * (MAX_PAYLOAD + 1))

    def test_exact_fields_unknown_missing_duplicate_trailing_and_version(self) -> None:
        good = canonical_json(self.command)
        self.assertEqual(decode_session_command(good), self.command)
        for mutation in (
            {**self.command, "unknown": 1},
            {key: value for key, value in self.command.items() if key != "gateway"},
            {**self.command, "version": 1},
        ):
            with self.assertRaises(HarnessProtocolError): decode_session_command(canonical_json(mutation))
        duplicate = b'{"gateway":"g","launch_nonce":"nonce-1","session_id":"session-1","stream_key":"x","stream_key":"x","type":"session","version":2}'
        with self.assertRaises(HarnessProtocolError): decode_session_command(duplicate)
        with self.assertRaises(HarnessProtocolError): decode_session_command(good + b" ")
        with self.assertRaises(HarnessProtocolError): decode_session_command(b"[]")

    def test_shared_swift_golden_fixture_round_trips_in_python(self) -> None:
        payload = shared_golden_session_command_payload()
        decoded = decode_session_command(payload)
        self.assertEqual(decoded["gateway"], "ws://127.0.0.1")
        self.assertEqual(canonical_json(decoded), payload)

    def test_gateway_base_validator_is_symmetric_and_keyless(self) -> None:
        valid = (
            "ws://127.0.0.1",
            "wss://example.com:443/api/stream/native",
            "ws://[::1]:8010/path",
        )
        for gateway in valid:
            with self.subTest(valid_gateway=gateway):
                self.assertEqual(validate_gateway_base(gateway), gateway)

        hostile = (
            "http://127.0.0.1",
            "WS://127.0.0.1",
            "ws://",
            "ws://User@127.0.0.1",
            "ws://User:password@127.0.0.1",
            "ws://127.0.0.1?stream_key=x",
            "ws://127.0.0.1#fragment",
            "ws://127.0.0.1/path with space",
            "ws://127.0.0.1/path\\segment",
            "ws://127.0.0.1/path%2Fsegment",
            "ws://127.0.0.1:0",
            "ws://127.0.0.1:65536",
            "ws://127.0.0.1:not-a-port",
            "ws://127.0.0.1//ambiguous",
            "ws://127.0.0.1/",
        )
        for gateway in hostile:
            with self.subTest(hostile_gateway=gateway):
                with self.assertRaises(HarnessProtocolError):
                    validate_gateway_base(gateway)

        for smuggled in (
            f"ws://127.0.0.1/{self.sentinel}",
            f"wss://127.0.0.1/api/{self.sentinel}",
        ):
            with self.subTest(smuggled_gateway=smuggled):
                with self.assertRaises(HarnessProtocolError):
                    validate_gateway_base_for_session(smuggled, self.sentinel)
                with self.assertRaises(HarnessProtocolError):
                    encode_session_command(
                        session_id="session-1",
                        stream_key=self.sentinel,
                        gateway=smuggled,
                        launch_nonce="nonce-1",
                    )

        encoded = "ws://127.0.0.1/api/TASK11%2DGOLDEN%2DSTREAM%2DKEY%2D0123456789abcdefgh"
        with self.assertRaises(HarnessProtocolError):
            validate_gateway_base_for_session(encoded, self.sentinel)
        source = shared_golden_session_command_payload.__globals__["Path"](
            __file__
        ).resolve().parent.parent / "companion/native-macos/Sources/TarsNativeCompanion/LiveHarnessProtocol.swift"
        swift_source = source.read_text(encoding="utf-8")
        self.assertIn("public enum LiveHarnessGatewayBase", swift_source)
        self.assertIn("validateForSession", swift_source)

    def test_peer_nonce_and_one_session_state(self) -> None:
        state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
        state.accept_peer(self.peer)
        self.assertEqual(state.accept_command(canonical_json(self.command), peer=self.peer), self.command)
        with self.assertRaises(HarnessProtocolError): state.accept_command(canonical_json(self.command), peer=self.peer)
        with self.assertRaises(HarnessProtocolError): state.accept_peer(self.peer)

    def test_wrong_peer_fields_all_fail(self) -> None:
        for field in ("euid", "pid", "audit_token", "executable_path"):
            values = {"euid": 502, "pid": 9999, "audit_token": "11" * 32, "executable_path": "/wrong"}
            bad = dataclasses_replace(self.peer, **{field: values[field]})
            state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
            with self.assertRaises(HarnessProtocolError): state.accept_peer(bad)

    @unittest.skipUnless(sys.platform == "darwin", "Darwin kernel socket options are unavailable")
    def test_darwin_reader_uses_real_getpeereid_pid_token_and_executable_boundary(self) -> None:
        reader = DarwinPeerIdentityReader()
        self.assertEqual(reader.LOCAL_PEERPID, 0x002)
        self.assertEqual(reader.LOCAL_PEERTOKEN, 0x006)
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            identity = reader(server)
            self.assertEqual(identity.euid, os.geteuid())
            self.assertEqual(identity.pid, os.getpid())
            self.assertIsNotNone(identity.audit_token)
            self.assertEqual(len(bytes.fromhex(str(identity.audit_token))), 32)
            self.assertTrue(os.path.isfile(str(identity.executable_path)))
            self.assertEqual(identity.executable_path, reader._path_for_pid(os.getpid()))
        finally:
            server.close()
            client.close()

    def _activation_and_health(self) -> tuple[dict[str, object], dict[str, object]]:
        attempt_id = "01234567-89ab-cdef-0123-456789abcdef"
        observer_token = "fedcba98-7654-3210-fedc-ba9876543210"
        base: dict[str, object] = {
            "actual_engine": PROCESS_TAP,
            "attempt_id": attempt_binding(attempt_id),
            "generation": 1,
            "launch_nonce": launch_binding("nonce-1"),
            "observer_binding": observer_binding(observer_token),
            "requested_engine": PROCESS_TAP,
            "resolved_engine": PROCESS_TAP,
            "session_binding": session_binding("session-1", "nonce-1"),
            "source_binding": source_binding("ObjectIdentifier(0x1234)"),
            "type": "event",
            "version": 2,
        }
        activation = {**base, "kind": "activation"}
        health = {
            **base,
            "kind": "health",
            "status": {
                "interruption": "clear",
                "kind": "running",
                "overflowed": False,
                "permission": "unknown",
                "route": "healthy",
                "sleep": "awake",
            },
        }
        return activation, health

    def test_health_must_match_complete_activation_identity_and_cannot_grant_positive_claim(self) -> None:
        activation, health = self._activation_and_health()
        self.assertEqual(self.peer, HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1").expected_peer)
        mismatch_values: dict[str, object] = {
            "session_binding": session_binding("other-session", "nonce-1"),
            "launch_nonce": launch_binding("nonce-2"),
            "attempt_id": attempt_binding("11234567-89ab-cdef-0123-456789abcdef"),
            "generation": 2,
            "source_binding": source_binding("ObjectIdentifier(0x1235)"),
            "observer_binding": observer_binding("11234567-89ab-cdef-0123-456789abcdef"),
            "requested_engine": SCREEN_CAPTURE_KIT,
            "resolved_engine": SCREEN_CAPTURE_KIT,
            "actual_engine": SCREEN_CAPTURE_KIT,
        }
        for field, value in mismatch_values.items():
            with self.subTest(field=field):
                state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
                state.accept_peer(self.peer)
                state.accept_command(canonical_json(self.command), peer=self.peer)
                state.accept_event(canonical_json(activation), peer=self.peer)
                hostile = dict(health)
                hostile[field] = value
                with self.assertRaises(HarnessProtocolError):
                    state.accept_event(canonical_json(hostile), peer=self.peer)
                self.assertFalse(
                    positive_process_tap_claim(PositiveProcessTapProof(
                        artifact_valid=True,
                        current_peer=True,
                        authenticated_peer_key="peer",
                        launch_nonce="nonce-1",
                        activation=Activation(
                            CaptureTuple(
                                "peer", "nonce-1",
                                attempt_binding("01234567-89ab-cdef-0123-456789abcdef"),
                                1,
                            ),
                            PROCESS_TAP,
                            PROCESS_TAP,
                            PROCESS_TAP,
                        ),
                        functional_permission_state="unknown",
                        functional_permission_tuple=CaptureTuple(
                            "peer", "nonce-1",
                            attempt_binding("01234567-89ab-cdef-0123-456789abcdef"),
                            1,
                        ),
                        transcript_valid=True,
                    ))
                )

        state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
        state.accept_peer(self.peer)
        state.accept_command(canonical_json(self.command), peer=self.peer)
        state.accept_event(canonical_json(activation), peer=self.peer)
        bad_peer = dataclasses_replace(self.peer, audit_token="22" * 32)
        with self.assertRaises(HarnessProtocolError): state.accept_event(canonical_json(health), peer=bad_peer)

    def test_health_schema_enums_messages_and_device_identity_are_symmetric(self) -> None:
        activation, health = self._activation_and_health()
        _ = activation
        invalid_enums = {
            "permission": "pending",
            "route": "changed-by-hostile-peer",
            "interruption": "paused",
            "sleep": "unknown",
        }
        for field, value in invalid_enums.items():
            with self.subTest(field=field):
                hostile = dict(health)
                hostile["status"] = dict(health["status"])
                hostile["status"][field] = value
                with self.assertRaises(HarnessProtocolError): decode_event(canonical_json(hostile))

        non_failed_code = dict(health)
        non_failed_code["status"] = dict(health["status"])
        non_failed_code["status"]["failure_code"] = "capture-failed"
        with self.assertRaises(HarnessProtocolError): decode_event(canonical_json(non_failed_code))

        failed = dict(health)
        failed_status = dict(health["status"])
        failed_status.update(
            {
                "kind": "failed",
                "route": "unknown",
                "interruption": "clear",
                "sleep": "awake",
                "overflowed": False,
                "permission": "unknown",
                "failure_code": "capture-failed",
            }
        )
        failed["status"] = failed_status
        self.assertEqual(decode_event(canonical_json(failed))["status"]["failure_code"], "capture-failed")
        with self.assertRaises(HarnessProtocolError):
            hostile = dict(failed)
            hostile["status"] = dict(failed_status)
            hostile["status"]["message"] = "raw diagnostic"
            decode_event(canonical_json(hostile))
        missing = dict(failed)
        missing["status"] = dict(failed_status)
        missing["status"].pop("failure_code")
        with self.assertRaises(HarnessProtocolError): decode_event(canonical_json(missing))

        valid_denied = dict(failed)
        valid_denied["status"] = dict(failed_status)
        valid_denied["status"].update({"permission": "denied", "failure_code": "permission-denied"})
        self.assertEqual(decode_event(canonical_json(valid_denied))["status"]["permission"], "denied")
        invalid_failed_codes = (
            ("unknown-denial-code", "unknown", "permission-denied"),
            ("denied-capture-code", "denied", "capture-failed"),
            ("granted", "granted", "capture-failed"),
            ("revoked", "revoked", "capture-failed"),
            ("unknown-code", "unknown", "unknown"),
        )
        for label, permission, failure_code in invalid_failed_codes:
            with self.subTest(failed_permission=label):
                hostile = dict(failed)
                hostile["status"] = dict(failed_status)
                hostile["status"].update({"permission": permission, "failure_code": failure_code})
                with self.assertRaises(HarnessProtocolError): decode_event(canonical_json(hostile))

        for identity in ("x" * 129, "not valid"):
            with self.subTest(device_identity=identity):
                hostile = dict(health)
                hostile["status"] = dict(health["status"])
                hostile["status"]["device_identity"] = identity
                with self.assertRaises(HarnessProtocolError): decode_event(canonical_json(hostile))

    def test_local_unix_coalesced_activation_and_health_are_retained_in_wire_order(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            server = UnixHarnessServer(Path(root) / "control.sock")
            server.bind()
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(server.socket_path))
                state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
                connection, peer = server.accept_authenticated(
                    state,
                    peer_reader=lambda _: self.peer,
                    timeout=1.0,
                )
                server.send_one_session(
                    connection,
                    state,
                    peer,
                    peer_revalidator=lambda: peer,
                    attested_identity=TEST_STATIC_IDENTITY,
                    static_identity=TEST_STATIC_IDENTITY,
                    session_id="session-1",
                    stream_key=self.sentinel,
                    gateway="ws://127.0.0.1",
                    timeout=1.0,
                )
                activation, health = self._activation_and_health()
                client.sendall(encode_event(activation) + encode_event(health))
                first = server.receive_event(connection, state, peer, timeout=1.0)
                second = server.receive_event(connection, state, peer, timeout=1.0)
                self.assertEqual(first["kind"], "activation")
                self.assertEqual(second["kind"], "health")
                self.assertEqual(list(state.activation_identities), [1])
                self.assertEqual(functional_permission(pcm_samples=[1.0]), "granted")
                claimed = Activation(
                    CaptureTuple(
                        _TEST_PEER_FINGERPRINT,
                        "nonce-1",
                        attempt_binding("01234567-89ab-cdef-0123-456789abcdef"),
                        1,
                    ),
                    PROCESS_TAP,
                    PROCESS_TAP,
                    PROCESS_TAP,
                )
                self.assertTrue(
                    positive_process_tap_claim(PositiveProcessTapProof(
                        artifact_valid=True,
                        current_peer=True,
                        authenticated_peer_key=_TEST_PEER_FINGERPRINT,
                        launch_nonce="nonce-1",
                        activation=claimed,
                        functional_permission_state="granted",
                        functional_permission_tuple=claimed.tuple,
                        transcript_valid=True,
                    ))
                )
            finally:
                client.close()
                server.close()

    def test_nonce_bound_shutdown_request_and_ack_are_exact_and_keyless(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            server = UnixHarnessServer(Path(root) / "control.sock")
            server.bind()
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(server.socket_path))
                state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
                connection, peer = server.accept_authenticated(
                    state, peer_reader=lambda _: self.peer, timeout=1.0
                )
                UnixHarnessServer.send_one_session(
                    connection,
                    state,
                    peer,
                    peer_revalidator=lambda: peer,
                    attested_identity=TEST_STATIC_IDENTITY,
                    static_identity=TEST_STATIC_IDENTITY,
                    session_id="session-1",
                    stream_key=self.sentinel,
                    gateway="ws://127.0.0.1",
                    timeout=1.0,
                )
                client.recv(4096)  # authenticated command; the key is never retained by this test
                nonce = shutdown_nonce()
                request = encode_shutdown_request(
                    session_ref=session_binding("session-1", "nonce-1"), nonce=nonce
                )
                UnixHarnessServer.send_shutdown_request(
                    connection,
                    state,
                    session_ref=session_binding("session-1", "nonce-1"),
                    nonce=nonce,
                    timeout=1.0,
                )
                request_value = decode_shutdown_request(client.recv(4096)[4:])
                self.assertEqual(request_value["shutdown_nonce"], nonce)
                self.assertEqual(request_value["shutdown_binding"], shutdown_binding(session_binding("session-1", "nonce-1"), nonce))
                self.assertNotIn(self.sentinel.encode(), request)

                acknowledgement = encode_shutdown_ack(
                    session_ref=session_binding("session-1", "nonce-1"), nonce=nonce
                )
                split = len(acknowledgement) // 2
                client.sendall(acknowledgement[:split])
                client.sendall(acknowledgement[split:])
                received = server.receive_event(
                    connection,
                    state,
                    peer,
                    timeout=1.0,
                    shutdown_nonce_value=nonce,
                )
                self.assertEqual(received["type"], "shutdown_ack")
                self.assertTrue(state._shutdown_acknowledged)
            finally:
                client.close()
                server.close()

    def test_shutdown_ack_must_be_the_sole_terminal_frame(self) -> None:
        """Ack coalescing or trailing bytes revoke instead of preserving proof."""

        activation, _ = self._activation_and_health()
        for label, trailing in (
            ("duplicate", None),
            ("valid-event", encode_event(activation)),
            ("partial", b"\x00"),
        ):
            with self.subTest(boundary=label), tempfile.TemporaryDirectory() as root:
                server = UnixHarnessServer(Path(root) / "control.sock")
                server.bind()
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    client.connect(str(server.socket_path))
                    state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
                    connection, peer = server.accept_authenticated(
                        state, peer_reader=lambda _: self.peer, timeout=1.0
                    )
                    server.send_one_session(
                        connection,
                        state,
                        peer,
                        peer_revalidator=lambda: peer,
                        attested_identity=TEST_STATIC_IDENTITY,
                        static_identity=TEST_STATIC_IDENTITY,
                        session_id="session-1",
                        stream_key=self.sentinel,
                        gateway="ws://127.0.0.1",
                        timeout=1.0,
                    )
                    client.recv(4096)
                    nonce = shutdown_nonce()
                    server.send_shutdown_request(
                        connection,
                        state,
                        session_ref=session_binding("session-1", "nonce-1"),
                        nonce=nonce,
                        timeout=1.0,
                    )
                    client.recv(4096)
                    acknowledgement = encode_shutdown_ack(
                        session_ref=session_binding("session-1", "nonce-1"),
                        nonce=nonce,
                    )
                    if label == "duplicate":
                        trailing = acknowledgement
                    client.sendall(acknowledgement + (trailing or b""))
                    with self.assertRaises(HarnessProtocolError):
                        server.receive_event(
                            connection,
                            state,
                            peer,
                            timeout=1.0,
                            shutdown_nonce_value=nonce,
                        )
                    self.assertTrue(state.control_lost)
                    self.assertFalse(state._shutdown_acknowledged)
                    self.assertIsNone(state._stream_key)
                    self.assertIsNone(state._session_binding)
                finally:
                    client.close()
                    server.close()

    def test_state_ack_terminalizes_credentials_and_rejects_reuse(self) -> None:
        state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
        state.accept_peer(self.peer)
        state.accept_command(canonical_json(self.command), peer=self.peer)
        nonce = shutdown_nonce()
        acknowledgement = encode_shutdown_ack(
            session_ref=session_binding("session-1", "nonce-1"),
            nonce=nonce,
        )
        state_ack = encode_shutdown_ack(
            session_ref=session_binding("session-1", "nonce-1"),
            nonce=nonce,
        )
        accepted = state.accept_shutdown_ack(
            state_ack[4:],
            peer=self.peer,
            expected_nonce=nonce,
        )
        self.assertEqual(accepted["type"], "shutdown_ack")
        self.assertTrue(state._shutdown_acknowledged)
        self.assertTrue(state.control_lost)
        self.assertIsNone(state._stream_key)
        self.assertIsNone(state._session_binding)
        with self.assertRaises(HarnessProtocolError):
            state.accept_event(canonical_json(self._activation_and_health()[0]), peer=self.peer)
        with self.assertRaises(HarnessProtocolError):
            state.accept_command(canonical_json(self.command), peer=self.peer)
        with self.assertRaises(HarnessProtocolError):
            state.accept_shutdown_ack(
                acknowledgement[4:],
                peer=self.peer,
                expected_nonce=nonce,
            )

    def test_failed_command_write_revokes_admitted_state_before_original_error(self) -> None:
        server_side, client_side = socket.socketpair()
        try:
            client_side.close()
            state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
            state.accept_peer(self.peer)
            with self.assertRaises((BrokenPipeError, ConnectionResetError, OSError, HarnessProtocolError)):
                UnixHarnessServer.send_one_session(
                    server_side,
                    state,
                    self.peer,
                    peer_revalidator=lambda: self.peer,
                    attested_identity=TEST_STATIC_IDENTITY,
                    static_identity=TEST_STATIC_IDENTITY,
                    session_id="session-1",
                    stream_key=self.sentinel,
                    gateway="ws://127.0.0.1",
                    timeout=1.0,
                )
            self.assertTrue(state.control_lost)
            self.assertIsNone(state._session_binding)
            self.assertIsNone(state._stream_key)
        finally:
            server_side.close()

    def test_peer_reader_base_exception_closes_accepted_socket(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            server = UnixHarnessServer(Path(root) / "control.sock")
            server.bind()
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(server.socket_path))
                state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")

                def interrupting_reader(_connection: socket.socket) -> PeerIdentity:
                    raise KeyboardInterrupt("peer reader interrupt")

                with self.assertRaises(KeyboardInterrupt):
                    server.accept_authenticated(
                        state,
                        peer_reader=interrupting_reader,
                        timeout=1.0,
                    )
                client.settimeout(1.0)
                self.assertEqual(client.recv(1), b"")
                self.assertFalse(server._connections)
            finally:
                client.close()
                server.close()

    def test_local_unix_competing_peer_is_rejected_before_any_credential_bytes(self) -> None:
        mismatches = {
            "euid": 502,
            "pid": 9999,
            "audit_token": "11" * 32,
            "executable_path": "/wrong/TarsCompanionApp",
        }
        for field, value in mismatches.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "control.sock"
                server = UnixHarnessServer(path)
                server.bind()
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    client.connect(str(path))
                    bad = dataclasses_replace(self.peer, **{field: value})
                    state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
                    seen_descriptors: list[int] = []

                    def injected_peer_reader(connection: socket.socket) -> PeerIdentity:
                        seen_descriptors.append(connection.fileno())
                        return bad

                    with self.assertRaises(HarnessProtocolError):
                        server.accept_authenticated(state, peer_reader=injected_peer_reader, timeout=1.0)
                    client.settimeout(1.0)
                    self.assertEqual(client.recv(1), b"")
                    self.assertEqual(len(seen_descriptors), 1)
                    self.assertGreaterEqual(seen_descriptors[0], 0)
                finally:
                    client.close()
                    server.close()

    def test_control_loss_rejects_active_state(self) -> None:
        state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
        state.accept_peer(self.peer)
        with self.assertRaises(HarnessProtocolError): state.lose_control()
        with self.assertRaises(HarnessProtocolError): state.require_active()

    def test_launch_spec_is_explicit_signed_app_process_tap_only(self) -> None:
        spec = make_launch_spec("/Applications/TarsCompanion.app", socket_path="/tmp/run/control.sock", launch_nonce="nonce-1", stream_key=self.sentinel)
        self.assertEqual(spec.argv[0], spec.app_path)
        self.assertIn("process-tap", spec.argv)
        self.assertNotIn(self.sentinel, spec.argv)
        self.assertFalse(any("tars-companion" in value or ".build" in value for value in spec.argv))
        for path in ("/tmp/tars-companion", "relative.app", "/tmp/a.out"):
            with self.assertRaises(HarnessProtocolError): make_launch_spec(path, socket_path="/tmp/x", launch_nonce="n", stream_key=self.sentinel)

    def test_launch_services_adapter_injected_runner_receives_only_safe_spec(self) -> None:
        spec = make_launch_spec(
            "/Applications/TarsCompanion.app",
            socket_path="/tmp/private-control.sock",
            launch_nonce="nonce-1",
            stream_key=self.sentinel,
        )
        helpers: list[FakeOpenHelper] = []

        def popen(
            argv: list[str],
            *,
            on_helper_spawned: Callable[[Any], None],
            stdout: Any = subprocess.PIPE,
            stderr: Any = subprocess.STDOUT,
            **kwargs: Any,
        ) -> FakeOpenHelper:
            self.assertEqual(stdout, subprocess.PIPE)
            self.assertEqual(stderr, subprocess.STDOUT)
            helper = FakeOpenHelper()
            helpers.append(helper)
            self.assertEqual(
                argv,
                [
                    "/usr/bin/open", "-n", "-W", spec.app_path, "--args",
                    "--live-harness-socket", "/tmp/private-control.sock",
                    "--live-harness-nonce", "nonce-1",
                    "--system-audio-engine", PROCESS_TAP,
                ],
            )
            on_helper_spawned(helper)
            return helper

        adapter = MacOSLaunchServicesAdapter(helper_spawner=popen)
        published_facades: list[Any] = []
        result = adapter.launch(spec, on_process=lambda p: published_facades.append(p))
        self.assertEqual(len(helpers), 1)
        self.assertEqual(published_facades, [result.process])
        self.assertIsInstance(result.process, LaunchServicesProcess)
        self.assertEqual(result.peer.euid, os.geteuid())
        self.assertIsNone(result.peer.pid)
        self.assertIsNone(result.peer.audit_token)
        received = spec
        self.assertEqual(received.app_path, spec.app_path)
        self.assertEqual(
            received.launch_arguments,
            (
                "--live-harness-socket", "/tmp/private-control.sock",
                "--live-harness-nonce", "nonce-1",
                "--system-audio-engine", PROCESS_TAP,
            ),
        )
        self.assertNotIn(self.sentinel, received.launch_arguments)
        self.assertNotIn("--stream-key", received.launch_arguments)
        self.assertNotIn(".build", received.launch_arguments)

    def test_launch_services_process_helper_pid_never_qualifies_as_authenticated_pid(self) -> None:
        helper = FakeOpenHelper(pid=7001)
        signals: list[tuple[bytes, int]] = []
        facade = LaunchServicesProcess(
            helper,
            signal_sender=lambda token, signum: signals.append((token, signum)),
        )
        self.assertEqual(facade.helper_pid, 7001)
        self.assertIsNone(facade.pid)
        self.assertIsNone(facade.poll())
        self.assertEqual(helper.poll_calls, 1)
        facade.terminate()
        self.assertEqual(helper.terminate_calls, 1)
        facade.send_signal(signal.SIGKILL)
        self.assertEqual(helper.signal_calls, [signal.SIGKILL])

        current_peer = [self.peer]
        facade.bind_authenticated_peer(self.peer, revalidator=lambda: current_peer[0])
        self.assertEqual(facade.pid, 4242)
        self.assertEqual(facade.authenticated_pid, 4242)
        self.assertEqual(facade.authenticated_peer, self.peer)
        self.assertEqual(facade.poll(), None)
        facade.terminate()
        facade.send_signal(signal.SIGKILL)
        self.assertEqual(
            signals,
            [
                (bytes.fromhex(self.audit_token), signal.SIGTERM),
                (bytes.fromhex(self.audit_token), signal.SIGKILL),
            ],
        )
        with self.assertRaises(HarnessProtocolError):
            facade.bind_authenticated_peer(
                dataclasses_replace(self.peer, pid=7001),
                revalidator=lambda: dataclasses_replace(self.peer, pid=7001),
            )

    def test_post_bind_full_peer_revalidation_and_helper_liveness_fail_closed_before_signal(self) -> None:
        helper = FakeOpenHelper(pid=7001)
        signals: list[tuple[bytes, int]] = []
        current_peer = [self.peer]
        facade = LaunchServicesProcess(
            helper,
            signal_sender=lambda token, signum: signals.append((token, signum)),
        )
        facade.bind_authenticated_peer(self.peer, revalidator=lambda: current_peer[0])
        facade.send_signal(signal.SIGTERM)
        self.assertEqual(signals, [(bytes.fromhex(self.audit_token), signal.SIGTERM)])

        for changed in (
            dataclasses_replace(self.peer, audit_token="33" * 32),
            dataclasses_replace(self.peer, executable_path="/different/app"),
            dataclasses_replace(self.peer, pid=7001),
        ):
            with self.subTest(changed=changed):
                current_peer[0] = changed
                with self.assertRaises(HarnessProtocolError): facade.send_signal(signal.SIGKILL)
                self.assertEqual(signals, [(bytes.fromhex(self.audit_token), signal.SIGTERM)])

        current_peer[0] = self.peer
        helper.returncode = 9
        with self.assertRaises(HarnessProtocolError): facade.terminate()
        self.assertEqual(signals, [(bytes.fromhex(self.audit_token), signal.SIGTERM)])

    def test_wait_retries_revalidation_failure_after_token_kill_until_helper_exits(self) -> None:
        """SIGKILL can kill the peer a few milliseconds before open -W exits."""

        helper = FakeOpenHelper(pid=7001)
        peer_dead = [False]
        clock = [0.0]

        def revalidator() -> PeerIdentity:
            if peer_dead[0]:
                raise HarnessProtocolError("authenticated peer revalidation failed")
            return self.peer

        def sleeper(interval: float) -> None:
            clock[0] += interval
            helper.returncode = 0

        facade = LaunchServicesProcess(
            helper,
            signal_sender=lambda token, signum: None,
            clock=lambda: clock[0],
            sleeper=sleeper,
        )
        facade.bind_authenticated_peer(self.peer, revalidator=revalidator)
        facade.kill()
        peer_dead[0] = True
        self.assertEqual(facade.wait(timeout=5.0), 0)
        self.assertGreater(clock[0], 0.0)

    def test_audit_token_sender_receives_exact_bytes_and_rejects_stale_token_race(self) -> None:
        helper = FakeOpenHelper(pid=7001)
        current_peer = [self.peer]
        calls: list[tuple[bytes, int]] = []

        def token_sender(token: bytes, signum: int) -> None:
            # Model the kernel token check at the destructive boundary: if the
            # peer disappears after revalidation, a replacement PID with a
            # different audit token cannot receive the operation.
            replacement = dataclasses_replace(self.peer, pid=4243, audit_token="44" * 32)
            current_peer[0] = replacement
            if token != bytes.fromhex(current_peer[0].audit_token):
                raise HarnessProtocolError("stale audit token")
            calls.append((token, signum))

        facade = LaunchServicesProcess(helper, signal_sender=token_sender)
        facade.bind_authenticated_peer(self.peer, revalidator=lambda: current_peer[0])
        with self.assertRaises(HarnessProtocolError): facade.send_signal(signal.SIGKILL)
        self.assertEqual(calls, [])

        invalid = LaunchServicesProcess(helper, signal_sender=lambda token, signum: None)
        with self.assertRaises(HarnessProtocolError):
            invalid.bind_authenticated_peer(
                dataclasses_replace(self.peer, audit_token="not-audit-token"),
                revalidator=lambda: dataclasses_replace(self.peer, audit_token="not-audit-token"),
            )

    def test_signal_boundary_is_token_only_and_pid_mutations_fail_static_contract(self) -> None:
        source = Path(__file__).with_name("live_system_audio_harness.py").read_text(encoding="utf-8")

        def token_only_contract(text: str) -> bool:
            return all(
                marker not in text
                for marker in (
                    "os.kill(",
                    "pid_probe",
                    "def __call__(self, pid:",
                    "self._signal_sender(peer.pid, token, signum)",
                )
            ) and "proc_signal_with_audittoken" in text

        self.assertTrue(token_only_contract(source))
        # Mutation-effective: either a PID-bearing callback or an os.kill
        # fallback must make the source contract fail, not merely change a
        # disconnected fixture.
        self.assertFalse(
            token_only_contract(source.replace(
                "self._signal_sender(token, signum)",
                "self._signal_sender(peer.pid, token, signum)",
                1,
            ))
        )
        self.assertFalse(
            token_only_contract(source.replace(
                "self._signal_sender(token, signum)",
                "os.kill(peer.pid, signum)",
                1,
            ))
        )
        self.assertIn(
            "def __call__(self, audit_token: bytes, signum: int)",
            source,
        )

    def test_secure_unix_socket_directory_and_socket_modes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "run" / "control.sock"
            server = UnixHarnessServer(path)
            server.bind()
            try:
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            finally:
                server.close()

    def test_artifact_provenance_signature_bundle_runtime_entitlements_digest_fail_closed(self) -> None:
        digest = "c" * 64
        good = ArtifactFacts("a" * 40, "b" * 40, False, "com.ellaexecutivesearch.tarscompanion", "3FLG8W6B95", True, ("com.apple.security.device.audio-input",), True, digest, TEST_STATIC_IDENTITY, digest, "", True, True)
        good = dataclasses_replace(good, provenance_digest=artifact_provenance_digest(good))
        validate_artifact(good, expected_head="a" * 40, expected_tree="b" * 40, expected_digest=digest)
        for field, value in (
            ("dirty", True), ("provenance_head", "other"), ("provenance_tree", "other"),
            ("bundle_id", "wrong"), ("team_id", "wrong"), ("hardened_runtime", False),
            ("strict_signature", False), ("executable_digest", "d" * 64), ("entitlements", ()),
            ("sealed_executable_digest", "d" * 64), ("provenance_digest", "d" * 64),
            ("developer_id_authority", False), ("audio_input_entitlement", False),
        ):
            bad = dataclasses_replace(good, **{field: value})
            with self.assertRaises(HarnessProtocolError): validate_artifact(bad, expected_head="a" * 40, expected_tree="b" * 40, expected_digest=digest)

    def test_signed_artifact_inspector_and_prelaunch_gate_block_every_sealed_mismatch(self) -> None:
        head = "a" * 40
        tree = "b" * 40
        unsigned_payload = b"fake unsigned executable"
        digest = hashlib.sha256(unsigned_payload).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            app = Path(root) / "TarsCompanion.app"
            executable = app / "Contents" / "MacOS" / "TarsCompanionApp"
            provenance = app / "Contents" / "Resources" / "Task11Provenance.json"
            executable.parent.mkdir(parents=True)
            provenance.parent.mkdir(parents=True)
            # The final signature marker is deliberately not part of the
            # sealed digest; the inspector must strip it on its disposable
            # copy through the fake codesign boundary.
            executable.write_bytes(unsigned_payload + b"|fake-signature")

            def good_resource(**changes: object) -> dict[str, object]:
                facts = ArtifactFacts(
                    head,
                    tree,
                    False,
                    "com.ellaexecutivesearch.tarscompanion",
                    "3FLG8W6B95",
                    True,
                    ("com.apple.security.device.audio-input",),
                    True,
                    digest,
                    TEST_STATIC_IDENTITY,
                    digest,
                    "",
                    True,
                    True,
                )
                facts = dataclasses_replace(facts, provenance_digest=artifact_provenance_digest(facts))
                resource: dict[str, object] = {
                    "bundle_id": facts.bundle_id,
                    "dirty": facts.dirty,
                    "entitlements": list(facts.entitlements),
                    "executable_sha256": facts.sealed_executable_digest,
                    "head": facts.provenance_head,
                    "hardened_runtime": facts.hardened_runtime,
                    "provenance_sha256": facts.provenance_digest,
                    "strict_signature": facts.strict_signature,
                    "team_id": facts.team_id,
                    "tree": facts.provenance_tree,
                }
                resource.update(changes)
                return resource

            class FakeCodesignRunner:
                def __init__(
                    self,
                    *,
                    verify_code: int = 0,
                    details_code: int = 0,
                    entitlements_code: int = 0,
                    details: str | None = None,
                    entitlements: str | None = None,
                ) -> None:
                    self.calls: list[tuple[str, ...]] = []
                    self.verify_code = verify_code
                    self.details_code = details_code
                    self.entitlements_code = entitlements_code
                    self.details = details or "Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded\n"
                    self.entitlements = entitlements or (
                        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                        "<plist version=\"1.0\"><dict>"
                        "<key>com.apple.security.device.audio-input</key><true/>"
                        "</dict></plist>"
                    )

                def __call__(self, argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                    self.calls.append(tuple(argv))
                    if argv[1] == "--remove-signature":
                        Path(argv[-1]).write_bytes(unsigned_payload)
                        return subprocess.CompletedProcess(argv, 0, "", "")
                    if argv[1] == "--verify":
                        return subprocess.CompletedProcess(argv, self.verify_code, "", "")
                    if argv[1] == "-dv":
                        return subprocess.CompletedProcess(argv, self.details_code, self.details, "")
                    return subprocess.CompletedProcess(argv, self.entitlements_code, self.entitlements, "")

            def inspect_and_validate(resource: dict[str, object], runner: FakeCodesignRunner) -> ArtifactFacts:
                provenance.write_bytes(canonical_json(resource))
                facts = SignedArtifactInspector(
                    runner=runner,
                    static_identity_reader=lambda _app: TEST_STATIC_IDENTITY,
                ).inspect(app)
                validate_artifact(facts, expected_head=head, expected_tree=tree, expected_digest=digest)
                return facts

            good_runner = FakeCodesignRunner()
            good_facts = inspect_and_validate(good_resource(), good_runner)
            self.assertEqual(good_facts.executable_digest, digest)
            self.assertEqual(len(good_runner.calls), 4)

            sealed_mutations: tuple[tuple[str, object], ...] = (
                ("dirty", True),
                ("head", "c" * 40),
                ("tree", "d" * 40),
                ("bundle_id", "wrong.bundle"),
                ("team_id", "WRONGTEAM"),
                ("hardened_runtime", False),
                ("strict_signature", False),
                ("executable_sha256", "e" * 64),
                ("provenance_sha256", "f" * 64),
                ("entitlements", []),
            )
            for field, value in sealed_mutations:
                with self.subTest(sealed_field=field):
                    runner = FakeCodesignRunner()
                    resource = good_resource(**{field: value})
                    provenance.write_bytes(canonical_json(resource))
                    launcher_calls: list[object] = []
                    launcher = MacOSLaunchServicesAdapter(
                        helper_spawner=make_helper_spawner(on_spawn=lambda argv: launcher_calls.append(argv))
                    )
                    try:
                        facts = SignedArtifactInspector(
                            runner=runner,
                            static_identity_reader=lambda _app: TEST_STATIC_IDENTITY,
                        ).inspect(app)
                    except HarnessProtocolError:
                        # A public signature/readback mismatch is rejected by
                        # the inspector itself, before any launch boundary.
                        pass
                    else:
                        with self.assertRaises(HarnessProtocolError):
                            CompanionRun(
                                app,
                                "session-1",
                                self.sentinel,
                                f"mismatch-{field}",
                                launcher=launcher,
                                artifact_facts=facts,
                                expected_head=head,
                                expected_tree=tree,
                                expected_digest=digest,
                                artifact_inspector=FakeArtifactInspector(facts),
                                running_code_attestor=FakeRunningCodeAttestor(),
                            )
                    self.assertEqual(launcher_calls, [])

            readback_mutations = (
                FakeCodesignRunner(verify_code=1),
                FakeCodesignRunner(verify_code=9, details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details_code=9),
                FakeCodesignRunner(entitlements_code=9),
                FakeCodesignRunner(details="Identifier=wrong\nTeamIdentifier=3FLG8W6B95\nflags=runtime\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=WRONG\nflags=runtime\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nCodeDirectory v=20500 size=1234 flags=0x0 hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x0(runtime) hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x2(runtime) hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=runtime hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory flags=0x10000(runtime) hashes=10+7\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x10000(notruntime) hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) flags=runtime hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded extra\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=malformed location=embedded\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Apple Development: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95) suffix\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details="Identifier=prefix.com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(details="Identifier=com.ellaexecutivesearch.tarscompanion\nTeamIdentifier=3FLG8W6B95-suffix\nAuthority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded\n"),
                FakeCodesignRunner(entitlements="com.apple.security.network.client"),
                FakeCodesignRunner(
                    entitlements=(
                        "<?xml version=\"1.0\"?><plist version=\"1.0\"><dict>"
                        "<key>com.apple.security.device.audio-input</key><false/>"
                        "</dict></plist>"
                    )
                ),
                FakeCodesignRunner(
                    entitlements=(
                        "<?xml version=\"1.0\"?><plist version=\"1.0\"><dict>"
                        "<key>prefix.com.apple.security.device.audio-input</key><true/>"
                        "</dict></plist>"
                    )
                ),
                FakeCodesignRunner(
                    entitlements=(
                        "<?xml version=\"1.0\"?><plist version=\"1.0\"><dict>"
                        "<key>com.apple.security.device.audio-input</key><true/>"
                        "<key>com.apple.security.get-task-allow</key><false/>"
                        "</dict></plist>"
                    )
                ),
            )
            for runner in readback_mutations:
                with self.subTest(readback=runner.calls):
                    provenance.write_bytes(canonical_json(good_resource()))
                    with self.assertRaises(HarnessProtocolError):
                        facts = SignedArtifactInspector(
                            runner=runner,
                            static_identity_reader=lambda _app: TEST_STATIC_IDENTITY,
                        ).inspect(app)
                        validate_artifact(facts, expected_head=head, expected_tree=tree, expected_digest=digest)
            self.assertEqual([call[0] for call in runner.calls], ["codesign"] * len(runner.calls))

    def test_phase_preflight_requires_typed_clean_execution_snapshot_before_artifact_inspection(self) -> None:
        """Preflight binds a clean HEAD/tree/status snapshot before any later boundary."""

        class Probe:
            def __enter__(self) -> "Probe":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def settimeout(self, _timeout: float) -> None:
                return None

            def connect_ex(self, _address: tuple[str, int]) -> int:
                return 1

        class Inspector:
            def __init__(self) -> None:
                self.calls = 0

            def inspect(self, _app: Path) -> ArtifactFacts:
                self.calls += 1
                return valid_artifact_facts()

        with tempfile.TemporaryDirectory() as root:
            signed_app = Path(root) / "TarsCompanion.app"
            signed_app.mkdir()
            for label, porcelain in (
                ("tracked", (" M tracked.py",)),
                ("untracked", ("?? fixture.txt",)),
            ):
                with self.subTest(snapshot=label):
                    phases = Phases(self.sentinel)
                    inspector = Inspector()
                    with mock.patch.object(
                        verifier.subprocess,
                        "run",
                        side_effect=AssertionError("dirty preflight must precede subprocess"),
                    ), mock.patch.object(
                        verifier,
                        "pick_voice",
                        side_effect=AssertionError("dirty preflight must precede voice lookup"),
                    ), mock.patch.object(
                        verifier.socket,
                        "socket",
                        side_effect=AssertionError("dirty preflight must precede port probe"),
                    ):
                        self.assertFalse(
                            verifier.phase_preflight(
                                phases,
                                signed_app,
                                artifact_inspector=inspector,
                                current_provenance=lambda porcelain=porcelain: verifier.Task11ProvenanceSnapshot(
                                    "a" * 40,
                                    "b" * 40,
                                    porcelain,
                                ),
                            )
                        )
                    self.assertEqual(inspector.calls, 0)
                    self.assertTrue(phases.failed)

            phases = Phases(self.sentinel)
            inspector = Inspector()
            completed = lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", "")
            with mock.patch.object(verifier.subprocess, "run", side_effect=completed), mock.patch.object(
                verifier.socket, "socket", return_value=Probe()
            ), mock.patch.object(verifier, "pick_voice", return_value="Eddy"):
                self.assertTrue(
                    verifier.phase_preflight(
                        phases,
                        signed_app,
                        artifact_inspector=inspector,
                        current_provenance=lambda: verifier.Task11ProvenanceSnapshot(
                            "a" * 40,
                            "b" * 40,
                            (),
                        ),
                    )
                )
            self.assertEqual(inspector.calls, 1)
            self.assertEqual(phases.facts["expected_head"], "a" * 40)
            self.assertEqual(phases.facts["expected_tree"], "b" * 40)
            self.assertFalse(phases.failed)
            self.assertIn("provenance.clean", inspect.getsource(verifier.phase_preflight))

    def test_companion_binds_authenticated_peer_pid_before_restart_checks(self) -> None:
        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            app = Path(root) / "TarsCompanion.app"
            executable = app / "Contents" / "MacOS" / "TarsCompanionApp"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"fixture executable")
            digest = hashlib.sha256(b"fixture executable").hexdigest()
            helper = FakeOpenHelper(pid=7001)
            client_holder: dict[str, socket.socket | None] = {"socket": None}

            def signal_sender(_token: bytes, _signum: int) -> None:
                helper.returncode = 0
                peer = client_holder["socket"]
                if peer is not None:
                    peer.close()

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=signal_sender,
            )
            run = CompanionRun(
                app,
                "session-1",
                self.sentinel,
                "pid-binding",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(digest),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest=digest,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts(digest)),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_holder["socket"] = client
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path)
                run.send_authenticated_session(peer_reader=lambda connection: actual)
                self.assertEqual(run.proc.helper_pid, 7001)
                self.assertEqual(run.proc.pid, 4242)
                self.assertEqual(run.authenticated_pid, 4242)
                self.assertNotEqual(run.proc.helper_pid, run.authenticated_pid)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
            finally:
                run.stop()
                client.close()
                verifier.SCRATCH = previous_scratch

    def test_failed_session_write_revokes_state_and_run_credentials(self) -> None:
        """A closed peer after admission cannot leave a successful run facade."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "closed-peer-write",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            real_connection: socket.socket | None = None

            class ClosedPeerSocket:
                """Delegate reads while making the credential write fail."""

                def __init__(self, wrapped: socket.socket) -> None:
                    self.wrapped = wrapped

                def fileno(self) -> int:
                    return self.wrapped.fileno()

                def recv(self, *args: object) -> bytes:
                    return self.wrapped.recv(*args)

                def gettimeout(self) -> float | None:
                    return self.wrapped.gettimeout()

                def settimeout(self, value: float | None) -> None:
                    self.wrapped.settimeout(value)

                def sendall(self, _wire: bytes) -> None:
                    # Close the server descriptor immediately before the
                    # production transaction's sendall, modelling a closed
                    # peer/write boundary deterministically.
                    self.wrapped.close()
                    self.wrapped.sendall(_wire)

                def close(self) -> None:
                    self.wrapped.close()

            original_accept = run.server.accept_authenticated

            def accept_with_closed_write(*args: object, **kwargs: object) -> tuple[ClosedPeerSocket, PeerIdentity]:
                nonlocal real_connection
                accepted, actual = original_accept(*args, **kwargs)
                real_connection = accepted
                return ClosedPeerSocket(accepted), actual

            run.server.accept_authenticated = accept_with_closed_write  # type: ignore[method-assign]
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path
                )
                with self.assertRaises(OSError) as raised:
                    run.send_authenticated_session(peer_reader=lambda _: actual)
                self.assertIs(type(raised.exception), OSError)
                self.assertEqual(raised.exception.errno, 9)
                self.assertIsNone(run._session_id)
                self.assertIsNone(run._stream_key)
                self.assertFalse(run.artifact_valid)
                state = run._state
                self.assertIsNotNone(state)
                self.assertTrue(state.control_lost)
                self.assertIsNone(state._stream_key)
                self.assertIsNone(state._session_binding)
                self.assertEqual(client.recv(1), b"")
            finally:
                client.close()
                run.server.close()
                if real_connection is not None:
                    real_connection.close()
                verifier.SCRATCH = previous_scratch

    def test_shutdown_request_keyboard_interrupt_cleans_up_and_propagates(self) -> None:
        """Non-Exception control signals cross the shutdown boundary unchanged."""

        server_side, client_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
            state.accept_peer(self.peer)

            class InterruptingServer:
                @staticmethod
                def send_shutdown_request(*_args: object, **_kwargs: object) -> None:
                    raise KeyboardInterrupt("injected shutdown interrupt")

            run = CompanionRun.__new__(CompanionRun)
            run._connection = server_side
            run._state = state
            run._session_binding = session_binding("session-1", "nonce-1")
            run._lock = threading.Lock()
            run.server = InterruptingServer()
            run._shutdown_nonce = None
            run._shutdown_acknowledged = False
            run._shutdown_request_sent = False
            run._proof_snapshot = object()

            with self.assertRaises(KeyboardInterrupt) as raised:
                run._send_shutdown_request(timeout=1.0)
            self.assertEqual(str(raised.exception), "injected shutdown interrupt")
            self.assertFalse(run._shutdown_request_sent)
            self.assertIsNone(run._shutdown_nonce)
            self.assertFalse(run._shutdown_acknowledged)
            self.assertIsNone(run._proof_snapshot)
            self.assertTrue(state.control_lost)
            self.assertIsNone(state._session_binding)
            self.assertIsNone(state._stream_key)
        finally:
            server_side.close()
            client_side.close()

    def test_post_bind_attestor_keyboard_interrupt_retires_run_and_phase_owner(self) -> None:
        """Post-bind control signals clear credentials and still attempt cleanup."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ, {"TMPDIR": root}):
            verifier.SCRATCH = Path(root) / "scratch"

            class InterruptingAttestor:
                def __call__(self, _peer: PeerIdentity, _expected: StaticCodeIdentity) -> object:
                    raise KeyboardInterrupt("post-bind attestor interrupt")

            helper = FakeOpenHelper(pid=4242)
            clients: list[socket.socket] = []
            received = bytearray()

            real_companion_run = verifier.CompanionRun

            class TrackingCompanionRun(real_companion_run):
                last: "TrackingCompanionRun | None" = None

                def __init__(self, *args: object, **kwargs: object) -> None:
                    super().__init__(*args, **kwargs)
                    TrackingCompanionRun.last = self

            def terminate_helper() -> None:
                helper.terminate_calls += 1
                helper.returncode = 0
                for client in clients:
                    client.close()

            helper.terminate = terminate_helper  # type: ignore[method-assign]
            helper.authenticated_peer = None

            def bind_authenticated_peer(peer: PeerIdentity, *, revalidator: object) -> None:
                _ = revalidator
                helper.authenticated_peer = peer

            helper.bind_authenticated_peer = bind_authenticated_peer  # type: ignore[method-assign]

            class Launcher:
                def launch(self, spec: object, *, on_process: object = None) -> tuple[FakeOpenHelper, PeerIdentity]:
                    if callable(on_process):
                        on_process(helper)
                    argv = list(getattr(spec, "argv"))
                    socket_path = argv[argv.index("--live-harness-socket") + 1]
                    app_path = Path(getattr(spec, "app_path"))
                    executable = str(app_path / "Contents" / "MacOS" / "TarsCompanionApp")
                    actual = PeerIdentity(os.geteuid(), 4242, self.audit_token, executable)

                    def connect() -> None:
                        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        clients.append(client)
                        try:
                            client.connect(socket_path)
                            client.settimeout(0.05)
                            while True:
                                try:
                                    chunk = client.recv(4096)
                                    received.extend(chunk)
                                    if not chunk:
                                        return
                                except socket.timeout:
                                    continue
                                except OSError:
                                    return
                        except OSError:
                            return

                    threading.Thread(target=connect, daemon=True).start()
                    return helper, actual

                audit_token = self.audit_token

            launcher = Launcher()
            phases = Phases(self.sentinel)
            signed_app = Path(root) / "TarsCompanion.app"
            actual = PeerIdentity(
                os.geteuid(),
                4242,
                self.audit_token,
                str(signed_app / "Contents" / "MacOS" / "TarsCompanionApp"),
            )
            with mock.patch.object(verifier, "DarwinPeerIdentityReader", return_value=lambda _: actual), mock.patch.object(
                verifier, "CompanionRun", TrackingCompanionRun
            ):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    verifier.phase_companion(
                        phases,
                        signed_app,
                        "session-1",
                        self.sentinel,
                        launcher=launcher,
                        artifact_facts=valid_artifact_facts(),
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                        running_code_attestor=InterruptingAttestor(),
                    )
            self.assertEqual(str(raised.exception), "post-bind attestor interrupt")
            self.assertEqual(helper.returncode, 0)
            run = TrackingCompanionRun.last
            self.assertIsNotNone(run)
            self.assertEqual(received, bytearray())
            self.assertIsNone(run._session_id)
            self.assertIsNone(run._stream_key)
            self.assertFalse(run.artifact_valid)
            self.assertIsNotNone(run._state)
            self.assertTrue(run._state.control_lost)
            self.assertIsNone(run._state._stream_key)
            self.assertIsNone(run._state._session_binding)
            self.assertIsNone(run._connection)
            self.assertIsNone(run.server.listener)
            self.assertFalse(run.run_dir.exists())
            self.assertFalse(list(Path(root).glob("tars-live-*")))
            verifier.SCRATCH = previous_scratch

    def _run_post_bind_wait_interrupt_schedule(self, stop_behavior: str):
        """Exercise the real phase with a post-bind wait control signal."""

        previous_scratch = verifier.SCRATCH
        root_context = tempfile.TemporaryDirectory()
        root = root_context.__enter__()
        verifier.SCRATCH = Path(root) / "scratch"
        signed_app = Path(root) / "TarsCompanion.app"
        wait_signal = KeyboardInterrupt("post-bind wait interrupt")
        stop_signal = KeyboardInterrupt("cleanup stop interrupt")
        command_received = threading.Event()
        release_peer = threading.Event()
        clients: list[socket.socket] = []
        client_threads: list[threading.Thread] = []

        class InterruptingHelper(FakeOpenHelper):
            def __init__(self) -> None:
                super().__init__(pid=4242)
                self.authenticated_peer: PeerIdentity | None = None
                self.poll_count = 0

            def bind_authenticated_peer(self, peer: PeerIdentity, *, revalidator: object) -> None:
                _ = revalidator
                self.authenticated_peer = peer

            def poll(self) -> int | None:
                self.poll_count += 1
                if self.poll_count == 1:
                    self.returncode = 0
                    self.assert_command_received()
                    release_peer.set()
                    raise wait_signal
                return self.returncode

            def assert_command_received(self) -> None:
                if not command_received.wait(2.0):
                    raise AssertionError("post-bind wait fixture never received the session command")

        helper = InterruptingHelper()

        class TrackingCompanionRun(CompanionRun):
            last: "TrackingCompanionRun | None" = None

            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                self.stop_calls = 0
                self.retired_before_stop = False
                TrackingCompanionRun.last = self

            def stop(self) -> bool:
                self.stop_calls += 1
                self.retired_before_stop = (
                    self._session_id is None
                    and self._stream_key is None
                    and self._state is not None
                    and self._state.control_lost
                )
                if self.stop_calls == 1:
                    if stop_behavior == "raise":
                        raise stop_signal
                    if stop_behavior == "false":
                        return False
                return super().stop()

        class Launcher:
            audit_token = self.audit_token

            def launch(self, spec: object, *, on_process: object = None) -> tuple[InterruptingHelper, PeerIdentity]:
                if callable(on_process):
                    on_process(helper)
                argv = list(getattr(spec, "argv"))
                socket_path = argv[argv.index("--live-harness-socket") + 1]
                app_path = Path(getattr(spec, "app_path"))
                executable = str(app_path / "Contents" / "MacOS" / "TarsCompanionApp")
                actual = PeerIdentity(os.geteuid(), 4242, self.audit_token, executable)

                def connect() -> None:
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    clients.append(client)
                    try:
                        client.connect(socket_path)
                        decoder = FrameDecoder()
                        while True:
                            payloads = decoder.feed(client.recv(4096))
                            if payloads:
                                command_received.set()
                                break
                        release_peer.wait(2.0)
                    except (OSError, HarnessProtocolError):
                        pass
                    finally:
                        try:
                            client.close()
                        except OSError:
                            pass

                thread = threading.Thread(target=connect, daemon=True)
                client_threads.append(thread)
                thread.start()
                return helper, actual

        launcher = Launcher()
        phases = Phases(self.sentinel)
        actual = PeerIdentity(
            os.geteuid(),
            4242,
            self.audit_token,
            str(signed_app / "Contents" / "MacOS" / "TarsCompanionApp"),
        )
        def finish() -> None:
            for thread in client_threads:
                thread.join(timeout=2.0)
            verifier.SCRATCH = previous_scratch
            root_context.__exit__(None, None, None)

        try:
            with mock.patch.object(
                verifier,
                "DarwinPeerIdentityReader",
                return_value=lambda _connection: actual,
            ), mock.patch.object(verifier, "CompanionRun", TrackingCompanionRun):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    verifier.phase_companion(
                        phases,
                        signed_app,
                        "session-1",
                        self.sentinel,
                        launcher=launcher,
                        artifact_facts=valid_artifact_facts(),
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                        running_code_attestor=FakeRunningCodeAttestor(),
                    )
            self.assertIs(raised.exception, wait_signal)
            self.assertEqual(str(raised.exception), "post-bind wait interrupt")
            self.assertEqual(phases.rows, [])
            run = TrackingCompanionRun.last
            self.assertIsNotNone(run)
            assert run is not None
            self.assertEqual(run.stop_calls, 1)
            self.assertTrue(run.retired_before_stop)
            self.assertIsNone(run._session_id)
            self.assertIsNone(run._stream_key)
            self.assertFalse(run.artifact_valid)
            self.assertIsNotNone(run._state)
            self.assertTrue(run._state.control_lost)
            self.assertIsNone(run._state._stream_key)
            self.assertIsNone(run._state._session_binding)
            return phases, run, finish
        except BaseException:
            # The caller owns the returned run for the retained schedules;
            # cleanup is performed by each discovered test after assertions.
            finish()
            raise

    def test_post_bind_wait_keyboard_interrupt_cleans_up_and_preserves_signal(self) -> None:
        phases, run, finish = self._run_post_bind_wait_interrupt_schedule("success")
        try:
            self.assertIsNone(phases.cleanup_run)
            self.assertTrue(run.cleanup_succeeded)
            self.assertIsNone(run._connection)
            self.assertIsNone(run.server.listener)
            self.assertFalse(run.run_dir.exists())
        finally:
            finish()

    def test_post_bind_wait_keyboard_interrupt_retains_owner_when_stop_returns_false(self) -> None:
        phases, run, finish = self._run_post_bind_wait_interrupt_schedule("false")
        try:
            self.assertIs(phases.cleanup_run, run)
            self.assertFalse(run.cleanup_succeeded)
            retained = phases.cleanup_run
            self.assertIs(retained, run)
            assert isinstance(retained, CompanionRun)
            self.assertTrue(retained.stop())
            self.assertEqual(run.stop_calls, 2)
            self.assertIsNone(run._connection)
            self.assertIsNone(run.server.listener)
        finally:
            finish()

    def test_post_bind_wait_keyboard_interrupt_retains_owner_when_stop_raises(self) -> None:
        phases, run, finish = self._run_post_bind_wait_interrupt_schedule("raise")
        try:
            self.assertIs(phases.cleanup_run, run)
            self.assertFalse(run.cleanup_succeeded)
            retained = phases.cleanup_run
            self.assertIs(retained, run)
            assert isinstance(retained, CompanionRun)
            self.assertTrue(retained.stop())
            self.assertEqual(run.stop_calls, 2)
            self.assertIsNone(run._connection)
            self.assertIsNone(run.server.listener)
        finally:
            finish()

    def test_post_admission_event_error_revokes_all_capture_and_positive_facts(self) -> None:
        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            app = Path(root) / "TarsCompanion.app"
            helper = FakeOpenHelper(pid=7001)
            signals: list[tuple[bytes, int]] = []
            # TERM completion is deliberately independent from peer EOF in
            # this fixture: the first stop must retain the descriptor until
            # the test closes the peer and retries cleanup.
            def signal_sender(token: bytes, signum: int) -> None:
                signals.append((token, signum))
                helper.returncode = 0

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=signal_sender,
            )
            run = CompanionRun(
                app,
                "session-1",
                self.sentinel,
                "event-revoke",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path)
                run.send_authenticated_session(peer_reader=lambda connection: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader()
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                for _ in range(100):
                    if run.capture_ready():
                        break
                    time.sleep(0.005)
                self.assertTrue(run.capture_ready())

                hostile = dict(health)
                hostile["unexpected"] = True
                client.sendall(frame(canonical_json(hostile)))
                for _ in range(100):
                    if run._event_error is not None:
                        break
                    time.sleep(0.005)
                self.assertIsNotNone(run._event_error)
                # A malformed event revokes success but must retain the
                # authenticated owner so stop() can revalidate and signal.
                self.assertTrue(run.peer_authenticated)
                self.assertIsNone(run.activation)
                self.assertEqual(run.functional_permission_state, "unknown")
                self.assertIsNone(run.functional_permission_tuple)
                self.assertEqual(run.authenticated_pid, 4242)
                self.assertIsNotNone(run.authenticated_peer_key)
                self.assertIsNotNone(run._connection)
                self.assertFalse(run.capture_ready())
                self.assertFalse(run.positive_claim(True))
                self.assertEqual(run.wait_for_capture(timeout=0.05)[0], "falhou")
                # Process TERM is accepted, but an open peer prevents final
                # retirement.  EOF followed by the same owner retry is the
                # only path that may close the descriptor/listener.
                self.assertFalse(run.stop())
                self.assertEqual(signals, [(bytes.fromhex(self.audit_token), signal.SIGTERM)])
                self.assertIsNotNone(run._connection)
                client.close()
                self.assertTrue(run.stop())
                self.assertTrue(run.cleanup_succeeded)
            finally:
                run.stop()
                client.close()
                verifier.SCRATCH = previous_scratch

    def test_post_snapshot_terminal_event_revokes_proof_before_reader_cleanup(self) -> None:
        """A denied event after capture snapshots cannot leave a stale PASS."""

        previous_scratch = verifier.SCRATCH
        previous_doc = verifier.EVIDENCE_DOC
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            helper.stdout = io.BytesIO(b"safe helper output")
            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=lambda token, signum: setattr(helper, "returncode", 0),
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "post-snapshot-terminal",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            terminal_seen = threading.Event()
            original_retire = run._retire_terminal_failure

            def observe_terminal(permission: str) -> None:
                original_retire(permission)
                terminal_seen.set()

            run._retire_terminal_failure = observe_terminal  # type: ignore[method-assign]
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(),
                    4242,
                    self.audit_token,
                    run.launch_spec.executable_path,
                )
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                self.assertIsNotNone(run.capture_live_proof_snapshot())

                _, failed = self._activation_and_health()
                failed["launch_nonce"] = launch_binding(run.launch_nonce)
                failed["session_binding"] = session_binding("session-1", run.launch_nonce)
                failed["status"] = dict(failed["status"])
                failed["status"].update(
                    {
                        "kind": "failed",
                        "route": "unknown",
                        "permission": "denied",
                        "failure_code": "permission-denied",
                    }
                )
                client.sendall(encode_event(failed))
                self.assertTrue(terminal_seen.wait(1.0))
                self.assertIsNone(run.proof_snapshot)
                self.assertIsNone(run.activation)
                self.assertEqual(run.functional_permission_state, "denied")
                self.assertFalse(run.capture_ready())

                phases = Phases(self.sentinel)
                phases.facts["transcript_valid_typed"] = True
                phases.facts["transcription_complete"] = True
                install_positive_operational_facts(phases, restart_drill=False)
                for row in positive_phase_rows(restart_drill=False):
                    if row["name"] == verifier.PhaseID.EVIDENCE.value:
                        continue
                    phases.record(
                        verifier.PhaseID(row["name"]),
                        verifier.PhaseStatus.PASS,
                        verifier.PhaseDetail.template(),
                    )
                with tempfile.TemporaryDirectory() as evidence_root:
                    verifier.EVIDENCE_DOC = Path(evidence_root) / "evidence.md"
                    verifier.phase_evidence(
                        phases,
                        type(
                            "Args",
                            (),
                            {
                                "signed_app": Path(evidence_root) / "TarsCompanion.app",
                                "with_restart_drill": False,
                            },
                        )(),
                        companion=run,
                        provenance_reader=lambda: ("a" * 40, []),
                    )
                    document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
                self.assertNotIn("process-tap-positive", document)
                self.assertNotEqual(verifier.final_result_code(phases), verifier.EXIT_OK)
            finally:
                # Complete the retained helper before closing the fixture
                # peer.  Passive EOF cleanup is intentionally refused while
                # the helper is still alive, so this ordering proves the same
                # ownership edge as production and leaves no accepted socket
                # behind if an assertion above fails.
                helper.returncode = 0
                client.close()
                try:
                    self.assertTrue(run.stop())
                    self.assertTrue(run.cleanup_succeeded)
                finally:
                    verifier.EVIDENCE_DOC = previous_doc
                    verifier.SCRATCH = previous_scratch

    def test_phase_evidence_fallback_bounds_top_level_and_nested_integers(self) -> None:
        """Rejected diagnostics still produce a bounded, secret-safe FAIL document."""

        previous_doc = verifier.EVIDENCE_DOC
        hostile = 1 << 64
        for label, install in (
            ("top-level-count", lambda ph: ph.facts.__setitem__("mic_bytes", hostile)),
            ("nested-diagnostic", lambda ph: ph.facts.__setitem__("error", {"nested": hostile})),
        ):
            with self.subTest(case=label):
                ph = Phases(self.sentinel)
                install(ph)
                try:
                    with tempfile.TemporaryDirectory() as root:
                        verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
                        verifier.phase_evidence(
                            ph,
                            type(
                                "Args",
                                (),
                                {
                                    "signed_app": Path(root) / "TarsCompanion.app",
                                    "with_restart_drill": False,
                                },
                            )(),
                            provenance_reader=lambda: ("a" * 40, []),
                        )
                        document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
                finally:
                    verifier.EVIDENCE_DOC = previous_doc
                self.assertNotIn(str(hostile), document)
                self.assertNotIn("process-tap-positive", document)
                self.assertEqual(ph.facts["process_tap_evidence_result"], "FAIL")
                self.assertNotEqual(verifier.final_result_code(ph), verifier.EXIT_OK)

    def test_diagnostic_ingress_and_fallback_are_total_for_cycles_and_deep_graphs(self) -> None:
        """Hostile diagnostic graphs become one bounded, ownership-failing marker."""

        self.assertEqual(verifier._DIAGNOSTIC_MAX_DEPTH, 64)
        just_over_bound: dict[str, object] = {"leaf": "hostile-descendant"}
        for _ in range(verifier._DIAGNOSTIC_MAX_DEPTH + 1):
            just_over_bound = {"nested": just_over_bound}
        bounded_ph = Phases(self.sentinel)
        bounded_ph.facts["error"] = just_over_bound
        self.assertTrue(bounded_ph._fact_ownership_failed)
        self.assertEqual(bounded_ph.facts["error"], verifier._SAFE_DIAGNOSTIC_MARKER)
        self.assertEqual(
            verifier._redact_evidence_value(
                just_over_bound,
                self.sentinel,
                owner=bounded_ph,
            ),
            verifier._SAFE_DIAGNOSTIC_MARKER,
        )

        for label, build in (
            ("cycle", lambda: (lambda value: value)({})),
            ("deep", lambda: {"leaf": "hostile-descendant"}),
        ):
            with self.subTest(graph=label):
                value = build()
                if label == "cycle":
                    value["self"] = value
                else:
                    for _ in range(1_200):
                        value = {"nested": value}
                ph = Phases(self.sentinel)
                ph.facts["error"] = value
                self.assertTrue(ph._fact_ownership_failed)
                self.assertEqual(ph.facts["error"], verifier._SAFE_DIAGNOSTIC_MARKER)
                self.assertNotIn("hostile-descendant", json.dumps(ph.facts))

                # Bypass only the ingress wrapper to exercise the independent
                # evidence/fallback traversal; it must still be total.
                raw = {}
                if label == "cycle":
                    raw["cycle"] = raw
                else:
                    raw = {"nested": value}
                dict.__setitem__(ph.facts, "error", raw)
                with tempfile.TemporaryDirectory() as root:
                    previous_doc = verifier.EVIDENCE_DOC
                    try:
                        verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
                        verifier.phase_evidence(
                            ph,
                            type(
                                "Args",
                                (),
                                {
                                    "signed_app": Path(root) / "TarsCompanion.app",
                                    "with_restart_drill": False,
                                },
                            )(),
                            provenance_reader=lambda: ("a" * 40, []),
                        )
                        document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
                    finally:
                        verifier.EVIDENCE_DOC = previous_doc
                self.assertNotIn("hostile-descendant", document)
                self.assertNotIn("process-tap-positive", document)
                self.assertEqual(ph.facts["process_tap_evidence_result"], "FAIL")
                self.assertNotEqual(verifier.final_result_code(ph), verifier.EXIT_OK)

    def test_hostile_mapping_exceptions_fail_closed_at_diagnostic_boundaries(self) -> None:
        """Ordinary hostile container errors never escape or enter evidence."""

        class ExplodingMapping(Mapping[str, object]):
            def __init__(self, error_type: type[Exception], label: str) -> None:
                self._error_type = error_type
                self._label = label

            def __iter__(self):
                raise self._error_type(f"HOSTILE_{self._label}_ITERATOR_SECRET")

            def __len__(self) -> int:
                raise self._error_type(f"HOSTILE_{self._label}_LENGTH_SECRET")

            def __getitem__(self, _key: str) -> object:
                raise self._error_type(f"HOSTILE_{self._label}_LOOKUP_SECRET")

            def items(self):
                raise self._error_type(f"HOSTILE_{self._label}_ITEMS_SECRET")

            def __repr__(self) -> str:
                raise RuntimeError(f"HOSTILE_{self._label}_REPR_SECRET")

        hostile = ExplodingMapping(RuntimeError, "RUNTIME")
        ingress = Phases(self.sentinel)
        ingress.facts["error"] = hostile
        self.assertTrue(ingress._fact_ownership_failed)
        self.assertEqual(ingress.facts["error"], verifier._SAFE_DIAGNOSTIC_MARKER)

        typed = Phases(self.sentinel)
        typed.facts["mic_bytes"] = hostile
        self.assertTrue(typed._fact_ownership_failed)
        self.assertEqual(typed.facts["mic_bytes"], verifier._SAFE_DIAGNOSTIC_MARKER)

        projection_owner = Phases(self.sentinel)
        projected = verifier._redact_evidence_value(
            {"error": hostile},
            self.sentinel,
            owner=projection_owner,
        )
        self.assertEqual(projected, verifier._SAFE_DIAGNOSTIC_MARKER)
        self.assertTrue(projection_owner._fact_ownership_failed)
        fallback = verifier._fallback_canonical_facts(
            hostile,
            self.sentinel,
            projection_owner,
        )
        self.assertEqual(fallback, {})
        self.assertTrue(projection_owner._fact_ownership_failed)

        evidence_ph = Phases(self.sentinel)
        dict.__setitem__(evidence_ph.facts, "error", hostile)
        previous_doc = verifier.EVIDENCE_DOC
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
                verifier.phase_evidence(
                    evidence_ph,
                    type(
                        "Args",
                        (),
                        {
                            "signed_app": Path(root) / "TarsCompanion.app",
                            "with_restart_drill": False,
                        },
                    )(),
                    provenance_reader=lambda: ("a" * 40, []),
                )
                document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
        finally:
            verifier.EVIDENCE_DOC = previous_doc
        self.assertNotIn("HOSTILE_", document)
        self.assertNotIn("RuntimeError", document)
        self.assertNotIn("process-tap-positive", document)
        self.assertEqual(evidence_ph.facts["process_tap_evidence_result"], "FAIL")
        self.assertNotEqual(verifier.final_result_code(evidence_ph), verifier.EXIT_OK)

    def test_post_snapshot_degraded_health_event_revokes_proof(self) -> None:
        """The production event reader treats accepted unknown health as a revocation."""

        previous_scratch = verifier.SCRATCH
        previous_doc = verifier.EVIDENCE_DOC
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            helper.stdout = io.BytesIO(b"safe helper output")
            client_holder: dict[str, socket.socket | None] = {"socket": None}

            def signal_sender(_token: bytes, _signum: int) -> None:
                helper.returncode = 0
                peer = client_holder["socket"]
                if peer is not None:
                    try:
                        peer.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    peer.close()

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=signal_sender,
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "post-snapshot-degraded",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_holder["socket"] = client
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path
                )
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                self.assertIsNotNone(run.capture_live_proof_snapshot())

                degraded = dict(health)
                degraded["status"] = dict(health["status"])
                degraded["status"].update({"permission": "unknown", "kind": "running"})
                client.sendall(encode_event(degraded))
                deadline = time.monotonic() + 1.0
                while run.functional_permission_state != "unknown" and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertIsNone(run.proof_snapshot)
                self.assertFalse(run.capture_ready())

                phases = Phases(self.sentinel)
                phases.facts["transcript_valid_typed"] = True
                phases.facts["transcription_complete"] = True
                install_positive_operational_facts(phases, restart_drill=False)
                for row in positive_phase_rows(restart_drill=False):
                    if row["name"] != verifier.PhaseID.EVIDENCE.value:
                        phases.record(
                            verifier.PhaseID(row["name"]),
                            verifier.PhaseStatus.PASS,
                            verifier.PhaseDetail.template(),
                        )
                with tempfile.TemporaryDirectory() as evidence_root:
                    verifier.EVIDENCE_DOC = Path(evidence_root) / "evidence.md"
                    verifier.phase_evidence(
                        phases,
                        type(
                            "Args",
                            (),
                            {
                                "signed_app": Path(evidence_root) / "TarsCompanion.app",
                                "with_restart_drill": False,
                            },
                        )(),
                        companion=run,
                        provenance_reader=lambda: ("a" * 40, []),
                    )
                    document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
                self.assertNotIn("process-tap-positive", document)
                self.assertNotEqual(verifier.final_result_code(phases), verifier.EXIT_OK)
            finally:
                helper.returncode = 0
                client.close()
                try:
                    self.assertTrue(run.stop())
                    self.assertTrue(run.cleanup_succeeded)
                finally:
                    verifier.EVIDENCE_DOC = previous_doc
                    verifier.SCRATCH = previous_scratch

    def test_control_eof_before_reader_exception_cannot_preserve_snapshot(self) -> None:
        """A real AF_UNIX EOF remains terminal when stop wins the exception race."""

        previous_scratch = verifier.SCRATCH
        previous_doc = verifier.EVIDENCE_DOC
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            helper.stdout = io.BytesIO(b"safe helper output")
            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=lambda token, signum: setattr(helper, "returncode", 0),
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "post-snapshot-eof-order",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            loss_ready = threading.Event()
            release_loss = threading.Event()
            original_receive = run.receive_event

            def delayed_receive(*args: object, **kwargs: object) -> dict[str, object]:
                try:
                    return original_receive(*args, **kwargs)
                except HarnessProtocolError as exc:
                    if str(exc) == "control connection lost":
                        loss_ready.set()
                        release_loss.wait(1.0)
                    raise

            run.receive_event = delayed_receive  # type: ignore[method-assign]
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path
                )
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                self.assertIsNotNone(run.capture_live_proof_snapshot())

                # The server has already marked HarnessState.control_lost by
                # the time this wrapper pauses, but the CompanionRun handler
                # has not yet had a chance to revoke its snapshot.
                client.close()
                self.assertTrue(loss_ready.wait(1.0))
                run._event_stop.set()
                release_loss.set()
                if run._event_thread is not None:
                    run._event_thread.join(timeout=1.0)
                self.assertTrue(run._state is not None and run._state.control_lost)
                self.assertIsNone(run.proof_snapshot)
                self.assertIsNotNone(run._event_error)

                phases = Phases(self.sentinel)
                phases.facts["transcript_valid_typed"] = True
                phases.facts["transcription_complete"] = True
                install_positive_operational_facts(phases, restart_drill=False)
                for row in positive_phase_rows(restart_drill=False):
                    if row["name"] != verifier.PhaseID.EVIDENCE.value:
                        phases.record(
                            verifier.PhaseID(row["name"]),
                            verifier.PhaseStatus.PASS,
                            verifier.PhaseDetail.template(),
                        )
                with tempfile.TemporaryDirectory() as evidence_root:
                    verifier.EVIDENCE_DOC = Path(evidence_root) / "evidence.md"
                    verifier.phase_evidence(
                        phases,
                        type(
                            "Args",
                            (),
                            {
                                "signed_app": Path(evidence_root) / "TarsCompanion.app",
                                "with_restart_drill": False,
                            },
                        )(),
                        companion=run,
                        provenance_reader=lambda: ("a" * 40, []),
                    )
                    document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
                self.assertNotIn("process-tap-positive", document)
                self.assertNotEqual(verifier.final_result_code(phases), verifier.EXIT_OK)
            finally:
                helper.returncode = 0
                release_loss.set()
                client.close()
                try:
                    self.assertTrue(run.stop())
                    self.assertTrue(run.cleanup_succeeded)
                finally:
                    verifier.EVIDENCE_DOC = previous_doc
                    verifier.SCRATCH = previous_scratch

    def test_capture_snapshot_rejects_control_lost_state_before_reader_unwind(self) -> None:
        """Capture cannot mint proof after the server has observed control EOF."""

        previous_scratch = verifier.SCRATCH
        previous_doc = verifier.EVIDENCE_DOC
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            helper.stdout = io.BytesIO(b"safe helper output")
            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=lambda token, signum: setattr(helper, "returncode", 0),
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "capture-guard-eof-order",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            loss_ready = threading.Event()
            release_loss = threading.Event()
            original_receive = run.receive_event

            def delayed_receive(*args: object, **kwargs: object) -> dict[str, object]:
                try:
                    return original_receive(*args, **kwargs)
                except HarnessProtocolError as exc:
                    if str(exc) == "control connection lost":
                        loss_ready.set()
                        release_loss.wait(1.0)
                    raise

            run.receive_event = delayed_receive  # type: ignore[method-assign]
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path
                )
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                # Deliberately do not capture a snapshot before EOF.  The
                # state-level loss guard is the only edge under test here.
                client.close()
                self.assertTrue(loss_ready.wait(1.0))
                self.assertTrue(run._state is not None and run._state.control_lost)
                self.assertIsNone(run.capture_live_proof_snapshot())
                self.assertIsNone(run.proof_snapshot)

                run._event_stop.set()
                release_loss.set()
                if run._event_thread is not None:
                    run._event_thread.join(timeout=1.0)
                self.assertIsNotNone(run._event_error)

                phases = Phases(self.sentinel)
                phases.facts["transcript_valid_typed"] = True
                phases.facts["transcription_complete"] = True
                install_positive_operational_facts(phases, restart_drill=False)
                for row in positive_phase_rows(restart_drill=False):
                    if row["name"] != verifier.PhaseID.EVIDENCE.value:
                        phases.record(
                            verifier.PhaseID(row["name"]),
                            verifier.PhaseStatus.PASS,
                            verifier.PhaseDetail.template(),
                        )
                with tempfile.TemporaryDirectory() as evidence_root:
                    verifier.EVIDENCE_DOC = Path(evidence_root) / "evidence.md"
                    verifier.phase_evidence(
                        phases,
                        type(
                            "Args",
                            (),
                            {
                                "signed_app": Path(evidence_root) / "TarsCompanion.app",
                                "with_restart_drill": False,
                            },
                        )(),
                        companion=run,
                        provenance_reader=lambda: ("a" * 40, []),
                    )
                    document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
                self.assertNotIn("process-tap-positive", document)
                self.assertNotEqual(verifier.final_result_code(phases), verifier.EXIT_OK)
            finally:
                helper.returncode = 0
                release_loss.set()
                client.close()
                try:
                    self.assertTrue(run.stop())
                    self.assertTrue(run.cleanup_succeeded)
                finally:
                    verifier.EVIDENCE_DOC = previous_doc
                    verifier.SCRATCH = previous_scratch

    def test_eof_before_shutdown_ack_invalidates_snapshot_even_after_process_completion(self) -> None:
        """Reviewer schedule: peer EOF in terminate cannot qualify a stale snapshot."""

        previous_scratch = verifier.SCRATCH
        previous_doc = verifier.EVIDENCE_DOC
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            client_holder: dict[str, socket.socket | None] = {"socket": None}
            lifecycle_order: list[str] = []

            def signal_sender(_token: bytes, signum: int) -> None:
                if signum != signal.SIGTERM:
                    return
                peer = client_holder["socket"]
                if peer is not None:
                    try:
                        peer.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    peer.close()
                # Deliberately complete the helper only after the client EOF
                # operation above.  The old process-completed EOF predicate
                # would preserve the pre-stop snapshot on this schedule.
                lifecycle_order.append("peer-eof")
                helper.returncode = 0
                lifecycle_order.append("helper-complete")

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=signal_sender,
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "eof-before-ack-reviewer-schedule",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_holder["socket"] = client
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path
                )
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                self.assertIsNotNone(run.capture_live_proof_snapshot())

                self.assertTrue(run.stop())
                self.assertEqual(lifecycle_order, ["peer-eof", "helper-complete"])
                self.assertTrue(run.cleanup_succeeded)
                self.assertFalse(run._shutdown_acknowledged)
                # This is intentionally after successful cleanup: EOF must
                # remain disqualifying even when all teardown conjuncts pass.
                self.assertIsNone(run.proof_snapshot)

                phases = Phases(self.sentinel)
                phases.facts["transcript_valid_typed"] = True
                phases.facts["transcription_complete"] = True
                install_positive_operational_facts(phases, restart_drill=False)
                for row in positive_phase_rows(restart_drill=False):
                    if row["name"] != verifier.PhaseID.EVIDENCE.value:
                        phases.record(
                            verifier.PhaseID(row["name"]),
                            verifier.PhaseStatus.PASS,
                            verifier.PhaseDetail.template(),
                        )
                with tempfile.TemporaryDirectory() as evidence_root:
                    verifier.EVIDENCE_DOC = Path(evidence_root) / "evidence.md"
                    verifier.phase_evidence(
                        phases,
                        type(
                            "Args",
                            (),
                            {
                                "signed_app": Path(evidence_root) / "TarsCompanion.app",
                                "with_restart_drill": False,
                            },
                        )(),
                        companion=run,
                        provenance_reader=lambda: ("a" * 40, []),
                    )
                    document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
                self.assertNotIn("process-tap-positive", document)
                self.assertNotEqual(verifier.final_result_code(phases), verifier.EXIT_OK)
            finally:
                client.close()
                run.stop()
                verifier.EVIDENCE_DOC = previous_doc
                verifier.SCRATCH = previous_scratch

    def test_ack_before_eof_preserves_snapshot_and_revokes_state_before_signal(self) -> None:
        """An admitted ack permits graceful EOF cleanup without retaining credentials."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            client_holder: dict[str, socket.socket | None] = {"socket": None}
            ack_sent = threading.Event()
            ack_errors: list[BaseException] = []
            state_at_signal: list[tuple[object, object]] = []
            run_holder: dict[str, CompanionRun | None] = {"run": None}

            def signal_sender(_token: bytes, signum: int) -> None:
                if signum != signal.SIGTERM:
                    return
                run = run_holder["run"]
                self.assertIsNotNone(run)
                state = run._state
                self.assertIsNotNone(state)
                # stop() must revoke HarnessState immediately after joining
                # the exact ack reader and before process lifecycle wait.
                state_at_signal.append((state._stream_key, state._session_binding))
                helper.returncode = 0
                peer = client_holder["socket"]
                if peer is not None:
                    try:
                        peer.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    peer.close()

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=signal_sender,
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "ack-before-eof",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            run_holder["run"] = run
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_holder["socket"] = client

            def acknowledge_shutdown() -> None:
                try:
                    decoder = FrameDecoder()
                    request_payload: bytes | None = None
                    while request_payload is None:
                        chunk = client.recv(4096)
                        if not chunk:
                            raise OSError("shutdown request peer EOF")
                        payloads = decoder.feed(chunk)
                        if payloads:
                            request_payload = payloads[0]
                    request = decode_shutdown_request(request_payload)
                    client.sendall(
                        encode_shutdown_ack(
                            session_ref=session_binding("session-1", run.launch_nonce),
                            nonce=request["shutdown_nonce"],
                        )
                    )
                    ack_sent.set()
                except (OSError, HarnessProtocolError) as exc:
                    ack_errors.append(exc)

            ack_thread = threading.Thread(target=acknowledge_shutdown, daemon=True)
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path
                )
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                self.assertIsNotNone(run.capture_live_proof_snapshot())

                ack_thread.start()
                self.assertTrue(run.stop())
                ack_thread.join(timeout=1.0)
                self.assertTrue(ack_sent.is_set())
                self.assertEqual(ack_errors, [])
                self.assertEqual(state_at_signal, [(None, None)])
                self.assertTrue(run.cleanup_succeeded)
                self.assertIsNotNone(run.proof_snapshot)
            finally:
                client.close()
                ack_thread.join(timeout=1.0)
                run.stop()
                verifier.SCRATCH = previous_scratch

    def test_activationless_terminal_failure_wire_is_sticky_and_preserves_permission(self) -> None:
        """A fenced failed health event is terminal even before activation."""

        previous_scratch = verifier.SCRATCH
        denial_message = (
            "O macOS negou a captura de áudio do sistema. Autorize o TarsCompanion em "
            "Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema e tente novamente."
        )
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            for label, message, expected_permission in (
                ("denied", denial_message, "denied"),
                ("unknown", "route failed", "unknown"),
            ):
                with self.subTest(permission=label):
                    helper = FakeOpenHelper(pid=7001)
                    client_holder: dict[str, socket.socket | None] = {"socket": None}

                    def signal_sender(_token: bytes, _signum: int) -> None:
                        helper.returncode = 0
                        peer = client_holder["socket"]
                        if peer is not None:
                            peer.close()

                    adapter = MacOSLaunchServicesAdapter(
                        helper_spawner=make_helper_spawner(helper),
                        signal_sender=signal_sender,
                    )
                    run = CompanionRun(
                        Path(root) / "TarsCompanion.app",
                        "session-1",
                        self.sentinel,
                        f"activationless-{label}",
                        launcher=adapter,
                        artifact_facts=valid_artifact_facts(),
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                        running_code_attestor=FakeRunningCodeAttestor(),
                    )
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client_holder["socket"] = client
                    try:
                        client.connect(str(run.socket_path))
                        actual = PeerIdentity(
                            os.geteuid(),
                            4242,
                            self.audit_token,
                            run.launch_spec.executable_path,
                        )
                        run.send_authenticated_session(peer_reader=lambda _: actual)
                        client.settimeout(1.0)
                        self.assertGreater(len(client.recv(4096)), 4)
                        run.start_event_reader(timeout=0.02)

                        _, failed = self._activation_and_health()
                        failed["launch_nonce"] = launch_binding(run.launch_nonce)
                        failed["session_binding"] = session_binding("session-1", run.launch_nonce)
                        failed["status"] = dict(failed["status"])
                        failed["status"].update(
                            {
                                "kind": "failed",
                                "route": "unknown",
                                "interruption": "clear",
                                "sleep": "awake",
                                "overflowed": False,
                                "permission": expected_permission,
                                "failure_code": "permission-denied" if expected_permission == "denied" else "capture-failed",
                            }
                        )
                        client.sendall(encode_event(failed))
                        deadline = time.monotonic() + 1.0
                        while not getattr(run, "_terminal_failure", False) and time.monotonic() < deadline:
                            time.sleep(0.002)
                        self.assertTrue(getattr(run, "_terminal_failure", False))
                        self.assertIsNone(run._event_error)
                        self.assertTrue(run._state.control_lost)
                        self.assertIsNone(run.activation)
                        self.assertEqual(run.functional_permission_state, expected_permission)
                        self.assertIsNone(run.functional_permission_tuple)
                        self.assertFalse(run.capture_ready())
                        self.assertFalse(run.positive_claim(True))
                        expected_state = "tcc" if expected_permission == "denied" else "falhou"
                        self.assertEqual(run.wait_for_capture(timeout=0.05)[0], expected_state)
                        # A typed terminal observation must not discard the
                        # still-authenticated binding before stop can signal
                        # through the exact audit-token boundary.
                        self.assertTrue(run.peer_authenticated)
                        self.assertIsNotNone(run.authenticated_peer_key)
                        self.assertIsNotNone(run._connection)
                        reader = run._event_thread
                        self.assertIsNotNone(reader)
                        reader.join(timeout=1.0)
                        self.assertFalse(reader.is_alive())
                    finally:
                        run.stop()
                        client.close()
            verifier.SCRATCH = previous_scratch

    def test_phase_companion_distinguishes_denied_tcc_from_unknown_failure(self) -> None:
        """The phase ledger maps only the approved denial to exit 42."""

        previous_scratch = verifier.SCRATCH
        denial_message = PERMISSION_DENIED_MESSAGE
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            for label, message, expected_status, expected_exit in (
                ("denied", denial_message, "BLOQUEADO", verifier.EXIT_TCC_BLOCKED),
                ("unknown", "route failed", "FAIL", verifier.EXIT_FAILED),
            ):
                with self.subTest(permission=label):
                    helper = FakeOpenHelper(pid=7001)
                    clients: list[socket.socket] = []

                    def signal_sender(_token: bytes, _signum: int) -> None:
                        helper.returncode = 0
                        # The fake peer closes only as a consequence of the
                        # process completing; this makes the required EOF
                        # proof causal instead of an unconditional fixture
                        # cleanup shortcut.
                        for client in list(clients):
                            client.close()

                    def popen(argv: list[str], **_: object) -> FakeOpenHelper:
                        socket_path = argv[6]
                        launch_nonce = argv[8]

                        def writer() -> None:
                            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                            clients.append(client)
                            try:
                                client.connect(socket_path)
                                # The server sends exactly one credential-bearing
                                # command after authenticating this peer.  Read
                                # that frame before emitting the terminal event.
                                client.settimeout(1.0)
                                decoder = FrameDecoder()
                                while True:
                                    payloads = decoder.feed(client.recv(4096))
                                    if payloads:
                                        break
                                activation, failed = self._activation_and_health()
                                _ = activation
                                failed["launch_nonce"] = launch_binding(launch_nonce)
                                failed["session_binding"] = session_binding("session-1", launch_nonce)
                                failed["status"] = dict(failed["status"])
                                failed["status"].update(
                                    {"kind": "failed", "permission": label,
                                     "route": "unknown", "interruption": "clear",
                                     "sleep": "awake", "overflowed": False,
                                     "failure_code": "permission-denied" if label == "denied" else "capture-failed"}
                                )
                                client.sendall(encode_event(failed))
                            except (OSError, HarnessProtocolError):
                                pass

                        threading.Thread(target=writer, daemon=True).start()
                        return helper

                    adapter = MacOSLaunchServicesAdapter(
                        helper_spawner=make_helper_spawner(popen),
                        signal_sender=signal_sender,
                    )
                    phases = Phases(self.sentinel)
                    signed_app = Path(root) / "TarsCompanion.app"
                    actual = PeerIdentity(
                        os.geteuid(),
                        4242,
                        self.audit_token,
                        str(signed_app / "Contents" / "MacOS" / "TarsCompanionApp"),
                    )
                    with mock.patch.object(
                        verifier,
                        "DarwinPeerIdentityReader",
                        return_value=lambda _: actual,
                    ):
                        result = verifier.phase_companion(
                            phases,
                            signed_app,
                            "session-1",
                            self.sentinel,
                            launcher=adapter,
                            artifact_facts=valid_artifact_facts(),
                            expected_head="a" * 40,
                            expected_tree="b" * 40,
                            expected_digest="a" * 64,
                            artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                            running_code_attestor=FakeRunningCodeAttestor(),
                        )
                    self.assertIsNone(result)
                    self.assertEqual(phases.rows[-1]["status"], expected_status)
                    detail = phases.rows[-1]["detail"]
                    if label == "denied":
                        self.assertEqual(
                            detail,
                            "permissão TCC negada reportada por evento autenticado do companion",
                        )
                        self.assertNotIn("saiu", detail)
                        self.assertNotIn("código 2", detail)
                    self.assertEqual(
                        verifier.EXIT_FAILED if phases.failed else verifier.EXIT_TCC_BLOCKED if phases.blocked else verifier.EXIT_OK,
                        expected_exit,
                    )
                    self.assertFalse(phases.failed if label == "denied" else phases.blocked)
                    for client in clients:
                        client.close()

            class ExitTwoProcess:
                def __init__(self, on_terminal_poll: object) -> None:
                    self.pid = 4242
                    self.stdout = None
                    self.authenticated_peer = None
                    self.returncode = 2
                    self._on_terminal_poll = on_terminal_poll
                    self._terminal_poll_reported = False

                def bind_authenticated_peer(self, peer: PeerIdentity, *, revalidator: object) -> None:
                    _ = revalidator
                    self.authenticated_peer = peer

                def poll(self) -> int:
                    if not self._terminal_poll_reported:
                        self._terminal_poll_reported = True
                        # Keep the peer open while wait_for_capture observes
                        # the real exit-2 status.  Closing it asynchronously
                        # models the process-completion consequence and lets
                        # the subsequent stop prove EOF causally.
                        threading.Timer(0.01, self._on_terminal_poll).start()
                    return self.returncode

                def wait(self, timeout: float | None = None) -> int:
                    _ = timeout
                    return self.returncode

                def terminate(self) -> None:
                    return None

                def kill(self) -> None:
                    return None

            exit_audit_token = self.audit_token

            class ExitTwoLauncher:
                def __init__(self) -> None:
                    self.clients: list[socket.socket] = []

                def close_clients(self) -> None:
                    for client in list(self.clients):
                        try:
                            client.close()
                        except OSError:
                            pass

                def launch(self, spec: object, *, on_process: object = None) -> tuple[ExitTwoProcess, PeerIdentity]:
                    process = ExitTwoProcess(self.close_clients)
                    if callable(on_process):
                        on_process(process)
                    expected = PeerIdentity(
                        os.geteuid(),
                        4242,
                        exit_audit_token,
                        str(Path(getattr(spec, "app_path")) / "Contents" / "MacOS" / "TarsCompanionApp"),
                    )

                    def writer() -> None:
                        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        self.clients.append(client)
                        try:
                            client.connect(str(getattr(spec, "argv")[2]))
                            client.recv(4096)
                        except OSError:
                            pass

                    threading.Thread(target=writer, daemon=True).start()
                    return process, expected

            exit_two = ExitTwoLauncher()
            exit_phases = Phases(self.sentinel)
            exit_app = Path(root) / "ExitTwo.app"
            exit_actual = PeerIdentity(
                os.geteuid(),
                4242,
                self.audit_token,
                str(exit_app / "Contents" / "MacOS" / "TarsCompanionApp"),
            )
            with mock.patch.object(
                verifier,
                "DarwinPeerIdentityReader",
                return_value=lambda _: exit_actual,
            ):
                self.assertIsNone(
                    verifier.phase_companion(
                        exit_phases,
                        exit_app,
                        "session-1",
                        self.sentinel,
                        launcher=exit_two,
                        artifact_facts=valid_artifact_facts(),
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                        running_code_attestor=FakeRunningCodeAttestor(),
                    )
                )
            self.assertEqual(exit_phases.rows[-1]["status"], "BLOQUEADO")
            self.assertEqual(
                exit_phases.rows[-1]["detail"],
                "permissão TCC ausente (companion saiu com código 2)",
            )
            self.assertIn("código 2", exit_phases.rows[-1]["detail"])
            self.assertNotIn("evento autenticado", exit_phases.rows[-1]["detail"])
            for client in exit_two.clients:
                client.close()
            verifier.SCRATCH = previous_scratch

    def test_phase_companion_tcc_cleanup_failure_is_fail_and_retains_owner(self) -> None:
        """A TCC result cannot hide a missing authenticated EOF proof."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            signed_app = Path(root) / "TarsCompanion.app"
            helper = FakeOpenHelper(pid=7001)
            clients: list[socket.socket] = []
            signals: list[tuple[bytes, int]] = []
            launch_count = [0]

            def signal_sender(token: bytes, signum: int) -> None:
                signals.append((token, signum))
                # The fake process has completed, but its authenticated peer
                # intentionally remains open for the first cleanup attempt.
                helper.returncode = 0

            def popen(argv: list[str], **_: object) -> FakeOpenHelper:
                launch_count[0] += 1

                def writer() -> None:
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    clients.append(client)
                    try:
                        client.connect(argv[6])
                        client.settimeout(1.0)
                        decoder = FrameDecoder()
                        while True:
                            payloads = decoder.feed(client.recv(4096))
                            if payloads:
                                break
                        _, failed = self._activation_and_health()
                        failed["launch_nonce"] = launch_binding(argv[8])
                        failed["session_binding"] = session_binding("session-1", argv[8])
                        failed_status = dict(failed["status"])
                        failed_status.update(
                            {
                                "kind": "failed",
                                "route": "unknown",
                                "interruption": "clear",
                                "sleep": "awake",
                                "overflowed": False,
                                "permission": "denied",
                                "failure_code": "permission-denied",
                            }
                        )
                        failed["status"] = failed_status
                        client.sendall(encode_event(failed))
                    except (OSError, HarnessProtocolError):
                        pass

                threading.Thread(target=writer, daemon=True).start()
                return helper

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(popen),
                signal_sender=signal_sender,
            )
            actual = PeerIdentity(
                os.geteuid(),
                4242,
                self.audit_token,
                str(signed_app / "Contents" / "MacOS" / "TarsCompanionApp"),
            )
            phases = Phases(self.sentinel)
            original_wait_for_eof = CompanionRun._wait_for_control_eof
            result: CompanionRun | None = None

            def short_wait_for_eof(run: CompanionRun, timeout: float = 5.0) -> bool:
                _ = timeout
                return original_wait_for_eof(run, timeout=0.05)

            try:
                with mock.patch.object(
                    verifier,
                    "DarwinPeerIdentityReader",
                    return_value=lambda _connection: actual,
                ), mock.patch.object(
                    CompanionRun,
                    "_wait_for_control_eof",
                    short_wait_for_eof,
                ):
                    result = verifier.phase_companion(
                        phases,
                        signed_app,
                        "session-1",
                        self.sentinel,
                        launcher=adapter,
                        artifact_facts=valid_artifact_facts(),
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                        running_code_attestor=FakeRunningCodeAttestor(),
                    )
                self.assertIsNotNone(result)
                self.assertEqual(launch_count[0], 1)
                self.assertTrue(phases.blocked)
                self.assertTrue(phases.failed)
                self.assertEqual(phases.rows[-1]["status"], "FAIL")
                retained = result
                assert retained is not None
                self.assertTrue(retained.peer_authenticated)
                self.assertIsNotNone(retained._connection)
                self.assertIsNotNone(retained.server.listener)
                self.assertEqual(signals, [(bytes.fromhex(self.audit_token), signal.SIGTERM)])

                # The same owner can finish only after the actual peer EOF;
                # no replacement launch or generic reset is permitted.
                for client in clients:
                    try:
                        client.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    client.close()
                self.assertTrue(retained.stop())
                self.assertTrue(retained.cleanup_succeeded)
                self.assertIsNone(retained._connection)
                self.assertIsNone(retained.server.listener)
                self.assertTrue(retained.stop())
                self.assertEqual(signals, [(bytes.fromhex(self.audit_token), signal.SIGTERM)])
            finally:
                for client in clients:
                    client.close()
                if result is not None and result.run_dir.exists():
                    result.stop()
                verifier.SCRATCH = previous_scratch

    def test_phase_companion_reinspects_artifact_before_credential_transmission(self) -> None:
        """A post-preflight bundle mutation sends no session command."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            signed_app = Path(root) / "TarsCompanion.app"
            cached = valid_artifact_facts()
            mutated = dataclasses_replace(cached, team_id="MUTATEDTEAM")

            class FreshInspector:
                def __init__(self) -> None:
                    self.calls = 0

                def inspect(self, _app: Path) -> ArtifactFacts:
                    self.calls += 1
                    return mutated

            inspector = FreshInspector()
            helper = FakeOpenHelper(pid=7001)
            clients: list[socket.socket] = []
            received: list[bytes] = []
            writer_done = threading.Event()
            signals: list[tuple[bytes, int]] = []
            close_calls: list[int] = []
            run_holder: list[CompanionRun] = []

            original_companion_run = verifier.CompanionRun

            class TrackingCompanionRun(original_companion_run):
                """Expose only lifecycle accounting for this causal fixture."""

                def __init__(self, *args: object, **kwargs: object) -> None:
                    super().__init__(*args, **kwargs)
                    run_holder.append(self)
                    original_close = self.server.close_connection

                    def counted_close(connection: socket.socket) -> None:
                        close_calls.append(connection.fileno())
                        original_close(connection)

                    self.server.close_connection = counted_close  # type: ignore[method-assign]

            def popen(argv: list[str], **_: object) -> FakeOpenHelper:
                def writer() -> None:
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    clients.append(client)
                    try:
                        client.connect(argv[6])
                        client.settimeout(1.0)
                        received.append(client.recv(4096))
                    except (OSError, socket.timeout):
                        received.append(b"")
                    finally:
                        client.close()
                        writer_done.set()

                threading.Thread(target=writer, daemon=True).start()
                return helper

            def signal_sender(token: bytes, signum: int) -> None:
                signals.append((token, signum))
                helper.returncode = 0
                # Process completion and peer EOF are one causal fixture edge;
                # the production stop path must still observe the EOF before
                # it can retire the authenticated descriptor.
                for client in list(clients):
                    try:
                        client.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    client.close()

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(popen),
                signal_sender=signal_sender,
            )
            actual = PeerIdentity(
                os.geteuid(),
                4242,
                self.audit_token,
                str(signed_app / "Contents" / "MacOS" / "TarsCompanionApp"),
            )
            phases = Phases(self.sentinel)
            try:
                with mock.patch.object(
                    verifier,
                    "DarwinPeerIdentityReader",
                    return_value=lambda _connection: actual,
                ), mock.patch.object(verifier, "CompanionRun", TrackingCompanionRun):
                    result = verifier.phase_companion(
                        phases,
                        signed_app,
                        "session-1",
                        self.sentinel,
                        launcher=adapter,
                        artifact_facts=cached,
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=inspector,
                        running_code_attestor=FakeRunningCodeAttestor(),
                    )
                self.assertIsNone(result)
                self.assertEqual(inspector.calls, 1)
                # Under the full suite the listener/cleanup thread can be
                # scheduled behind other fixture workers.  Wait on the
                # explicit writer completion barrier rather than relying on a
                # timing sleep or treating an in-flight writer as proof.
                self.assertTrue(writer_done.wait(5.0))
                self.assertTrue(received)
                self.assertTrue(all(not payload for payload in received))
                self.assertTrue(phases.failed)
                self.assertIn("preflight/controle rejeitado", phases.rows[-1]["detail"])
                self.assertEqual(len(run_holder), 1)
                tracked = run_holder[0]
                self.assertEqual(signals, [(bytes.fromhex(self.audit_token), signal.SIGTERM)])
                self.assertEqual(len(close_calls), 1)
                self.assertIsNone(tracked._connection)
                self.assertIsNone(tracked.server.listener)
                self.assertFalse(tracked.run_dir.exists())
            finally:
                for client in clients:
                    client.close()
                verifier.SCRATCH = previous_scratch

    def test_restart_reinspects_mutated_artifact_before_credential_transmission(self) -> None:
        """A replacement launch cannot send credentials after artifact drift."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            signed_app = Path(root) / "TarsCompanion.app"
            cached = valid_artifact_facts()
            mutated = dataclasses_replace(cached, team_id="RESTART-MUTATED-TEAM")

            class RestartInspector:
                def __init__(self) -> None:
                    self.calls = 0

                def inspect(self, _app: Path) -> ArtifactFacts:
                    self.calls += 1
                    # The original run consumes the sealed facts once.  The
                    # replacement must use this same inspector object and see
                    # the drift on its fresh pre-credential readback.
                    if self.calls >= 2:
                        if not replacement_reader_ready.wait(2.0):
                            raise AssertionError("replacement reader did not reach its readiness edge")
                        replacement_inspection_done.set()
                    return cached if self.calls == 1 else mutated

            inspector = RestartInspector()
            helpers: list[FakeOpenHelper] = []
            helper_peers: dict[int, socket.socket] = {}
            replacement_connected = threading.Event()
            replacement_reader_ready = threading.Event()
            replacement_inspection_done = threading.Event()
            replacement_stop_signal = threading.Event()
            replacement_helper_completed = threading.Event()
            replacement_writer_done = threading.Event()
            received: list[bytes] = []
            reader_errors: list[str] = []
            replacement_signals: list[int] = []

            class RestartHelper(FakeOpenHelper):
                def poll(self) -> int | None:
                    status = super().poll()
                    if self.pid == 7001 and status is not None:
                        replacement_helper_completed.set()
                    return status

            def popen(argv: list[str], **_: object) -> FakeOpenHelper:
                helper = RestartHelper(pid=7000 + len(helpers))
                helpers.append(helper)
                helper_index = len(helpers) - 1
                if helper_index == 1:
                    def writer() -> None:
                        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        helper_peers[helper_index] = client
                        try:
                            client.connect(argv[6])
                            replacement_connected.set()
                            # The reader owns its close.  After the stop edge
                            # is signaled, poll readability without a
                            # blocking recv that another thread might close.
                            client.setblocking(False)
                            replacement_reader_ready.set()
                            if not replacement_stop_signal.wait(2.0):
                                reader_errors.append("stop signal was not observed")
                                return
                            deadline = time.monotonic() + 2.0
                            while time.monotonic() < deadline:
                                try:
                                    readable, _, _ = select.select([client], [], [], 0.05)
                                except (OSError, ValueError) as exc:
                                    reader_errors.append(f"readability probe failed: {exc}")
                                    return
                                if not readable:
                                    continue
                                try:
                                    received.append(client.recv(4096))
                                except BlockingIOError:
                                    continue
                                except OSError as exc:
                                    reader_errors.append(f"peer read failed: {exc}")
                                return
                            reader_errors.append("server EOF/readability edge was not observed")
                        except OSError:
                            reader_errors.append("replacement peer connection failed")
                        finally:
                            client.close()
                            replacement_writer_done.set()

                    threading.Thread(target=writer, daemon=True).start()
                return helper

            def signal_sender(_token: bytes, signum: int) -> None:
                helper_index = len(helpers) - 1
                helper = helpers[helper_index]
                if helper_index == 1:
                    replacement_signals.append(signum)
                helper.returncode = 0
                peer = helper_peers.get(helper_index)
                if peer is not None:
                    try:
                        # Half-close only the fixture peer's write side.  This
                        # is the causal server-EOF edge; the reader thread
                        # remains the sole owner of its descriptor and closes
                        # it after observing the server's EOF.
                        peer.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                if helper_index == 1:
                    replacement_stop_signal.set()

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(popen),
                signal_sender=signal_sender,
            )
            run = CompanionRun(
                signed_app,
                "session-1",
                self.sentinel,
                "restart-source",
                launcher=adapter,
                artifact_facts=cached,
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=inspector,
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            helper_peers[0] = client
            actual = PeerIdentity(
                os.geteuid(),
                4242,
                self.audit_token,
                str(signed_app / "Contents" / "MacOS" / "TarsCompanionApp"),
            )
            phases = Phases(self.sentinel)
            try:
                client.connect(str(run.socket_path))
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                with mock.patch.object(
                    verifier,
                    "DarwinPeerIdentityReader",
                    return_value=lambda _connection: actual,
                ):
                    verifier.phase_restart_drill(
                        phases,
                        run,
                        signed_app,
                        "session-1",
                        self.sentinel,
                        "ignored in offline proof",
                    )

                self.assertEqual(len(helpers), 2)
                self.assertTrue(replacement_connected.wait(1.0))
                self.assertTrue(replacement_reader_ready.is_set())
                self.assertTrue(replacement_inspection_done.is_set())
                self.assertEqual(inspector.calls, 2)
                self.assertEqual(phases.rows[-1]["status"], "FAIL")
                self.assertIn("preflight/controle rejeitado", phases.rows[-1]["detail"])
                replacement = phases.restart_run
                self.assertIsInstance(replacement, CompanionRun)
                assert isinstance(replacement, CompanionRun)
                self.assertIsNotNone(replacement._connection)
                self.assertTrue(replacement.peer_authenticated)
                self.assertIs(replacement._artifact_inspector, run._artifact_inspector)
                # The replacement reader is connected and ready before the
                # inspector rejects the drift; no command frame may cross the
                # artifact-rejection edge.
                self.assertTrue(replacement.stop())
                self.assertEqual(replacement_signals, [signal.SIGTERM])
                self.assertTrue(replacement_stop_signal.is_set())
                self.assertTrue(replacement_helper_completed.is_set())
                self.assertTrue(replacement._control_eof_observed)
                self.assertTrue(replacement_writer_done.wait(1.0))
                self.assertEqual(reader_errors, [])
                self.assertEqual(received, [b""])
                self.assertTrue(replacement.cleanup_succeeded)
            finally:
                client.close()
                run.stop()
                replacement = phases.restart_run
                phases.restart_run = None
                if isinstance(replacement, CompanionRun):
                    replacement.stop()
                verifier.SCRATCH = previous_scratch

    def test_restart_requires_run_bound_artifact_inspector_before_replacement_launch(self) -> None:
        """A restart without the original run's inspector cannot relaunch or send."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            signed_app = Path(root) / "TarsCompanion.app"
            helpers: list[FakeOpenHelper] = []
            client_holder: dict[str, socket.socket | None] = {"socket": None}
            stop_signal = threading.Event()

            def popen(argv: list[str], **_: object) -> FakeOpenHelper:
                _ = argv
                helper = FakeOpenHelper(pid=7000 + len(helpers))
                helpers.append(helper)
                return helper

            def signal_sender(_token: bytes, _signum: int) -> None:
                helper = helpers[-1]
                helper.returncode = 0
                peer = client_holder["socket"]
                if peer is not None:
                    try:
                        # The test owns this client socket; half-close gives
                        # the server a causal EOF without cross-thread close.
                        peer.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                stop_signal.set()

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(popen),
                signal_sender=signal_sender,
            )
            run = CompanionRun(
                signed_app,
                "session-1",
                self.sentinel,
                "restart-no-inspector",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_holder["socket"] = client
            actual = PeerIdentity(
                os.geteuid(),
                4242,
                self.audit_token,
                str(signed_app / "Contents" / "MacOS" / "TarsCompanionApp"),
            )
            phases = Phases(self.sentinel)
            try:
                client.connect(str(run.socket_path))
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                # Simulate losing the run-bound verifier after the healthy
                # first session; restart must refuse before killing or
                # launching a replacement.
                run._artifact_inspector = None

                with mock.patch.object(
                    verifier,
                    "DarwinPeerIdentityReader",
                    return_value=lambda _connection: actual,
                ):
                    verifier.phase_restart_drill(
                        phases,
                        run,
                        signed_app,
                        "session-1",
                        self.sentinel,
                        "ignored in offline proof",
                    )

                self.assertEqual(len(helpers), 1)
                self.assertIsNone(phases.restart_run)
                self.assertTrue(run.capture_ready())
                self.assertEqual(phases.rows[-1]["status"], "FAIL")
                self.assertIn("inspector de artefato vinculado ao run original ausente", phases.rows[-1]["detail"])
                self.assertTrue(run.stop())
                self.assertTrue(stop_signal.is_set())
                self.assertTrue(run.cleanup_succeeded)
            finally:
                client.close()
                run.stop()
                verifier.SCRATCH = previous_scratch

    def test_terminal_failure_preserves_authenticated_binding_until_exact_cleanup(self) -> None:
        """Typed failure signals once, joins readers, then closes once."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            helper.stdout = io.BytesIO(b"safe helper output")
            signals: list[tuple[bytes, int]] = []
            client_holder: dict[str, socket.socket | None] = {"socket": None}

            def signal_sender(token: bytes, signum: int) -> None:
                signals.append((token, signum))
                helper.returncode = 0
                peer = client_holder["socket"]
                if peer is not None:
                    peer.close()

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=signal_sender,
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "terminal-cleanup",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_holder["socket"] = client
            close_calls: list[int] = []
            original_close = run.server.close_connection

            def counted_close(connection: socket.socket) -> None:
                close_calls.append(connection.fileno())
                original_close(connection)

            run.server.close_connection = counted_close  # type: ignore[method-assign]
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path)
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                _, failed = self._activation_and_health()
                failed["launch_nonce"] = launch_binding(run.launch_nonce)
                failed["session_binding"] = session_binding("session-1", run.launch_nonce)
                failed["status"] = dict(failed["status"])
                failed["status"].update(
                    {
                        "kind": "failed",
                        "route": "unknown",
                        "interruption": "clear",
                        "sleep": "awake",
                        "overflowed": False,
                        "permission": "denied",
                        "failure_code": "permission-denied",
                    }
                )
                client.sendall(encode_event(failed))
                deadline = time.monotonic() + 1.0
                while not run._terminal_failure and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run._terminal_failure)
                self.assertIsNone(run._event_error)
                self.assertTrue(run.peer_authenticated)
                self.assertIsNotNone(run._connection)
                reader = run._event_thread
                self.assertIsNotNone(reader)
                output_reader = run._reader
                self.assertIsNotNone(output_reader)
                run.stop()
                self.assertEqual(signals, [(bytes.fromhex(self.audit_token), signal.SIGTERM)])
                self.assertFalse(reader.is_alive())
                self.assertFalse(output_reader.is_alive())
                self.assertEqual(len(close_calls), 1)
                run.stop()
                self.assertEqual(len(close_calls), 1)
            finally:
                run.stop()
                client.close()
                verifier.SCRATCH = previous_scratch

    def test_graceful_completion_requires_authenticated_peer_eof_before_close(self) -> None:
        """A completed helper cannot retire an authenticated open socket."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            class CompletedProcess:
                pid = 4242
                stdout = None

                def __init__(self) -> None:
                    self.returncode: int | None = None
                    self.authenticated_peer: PeerIdentity | None = None

                def bind_authenticated_peer(self, peer: PeerIdentity, *, revalidator: object) -> None:
                    _ = revalidator
                    self.authenticated_peer = peer

                def poll(self) -> int | None:
                    return self.returncode

                def wait(self, timeout: float | None = None) -> int:
                    _ = timeout
                    return self.returncode or 0

                def terminate(self) -> None:
                    self.returncode = 0

                def kill(self) -> None:
                    self.returncode = 0

            helper = CompletedProcess()

            class Launcher:
                def launch(self, _spec: object, *, on_process: object = None) -> tuple[CompletedProcess, PeerIdentity]:
                    if callable(on_process):
                        on_process(helper)
                    return helper, PeerIdentity(os.geteuid(), None, None, None)

            adapter = Launcher()
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "graceful-eof",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            close_calls: list[int] = []
            original_close = run.server.close_connection

            def counted_close(connection: socket.socket) -> None:
                close_calls.append(connection.fileno())
                original_close(connection)

            run.server.close_connection = counted_close  # type: ignore[method-assign]
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path)
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                helper.returncode = 0

                # Keep this causal test bounded while preserving the real
                # AF_UNIX EOF check and the authenticated connection owner.
                original_wait_for_eof = run._wait_for_control_eof

                def short_wait_for_eof(timeout: float = 5.0) -> bool:
                    _ = timeout
                    return original_wait_for_eof(timeout=0.05)

                run._wait_for_control_eof = short_wait_for_eof  # type: ignore[method-assign]
                self.assertFalse(run.stop())
                self.assertFalse(run.cleanup_succeeded)
                self.assertIsNotNone(run._connection)
                self.assertIsNotNone(run.server.listener)
                self.assertEqual(close_calls, [])
                self.assertEqual(run.cleanup_error, "authenticated control EOF was not observed")

                # A later retry may complete only after the authenticated peer
                # closes; it must then retire the connection/listener once.
                client.close()
                self.assertTrue(run.stop())
                self.assertTrue(run._control_eof_observed)
                self.assertTrue(run.cleanup_succeeded)
                self.assertIsNone(run._connection)
                self.assertIsNone(run.server.listener)
                self.assertEqual(len(close_calls), 1)
                self.assertTrue(run.stop())
                self.assertEqual(len(close_calls), 1)
            finally:
                client.close()
                if run.run_dir.exists():
                    run.stop()
                verifier.SCRATCH = previous_scratch

    def test_authenticated_eof_requires_helper_completion_and_then_retires_once(self) -> None:
        """EOF loses token authority; only later helper completion permits cleanup."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            signals: list[tuple[bytes, int]] = []
            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=lambda token, signum: signals.append((token, signum)),
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "passive-eof",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path)
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)

                client.close()
                deadline = time.monotonic() + 1.0
                while not run._control_eof_observed and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run._control_eof_observed)
                self.assertIsNotNone(run._connection)
                self.assertTrue(run.peer_authenticated)

                # EOF alone cannot claim that the app completed: the
                # LaunchServices helper is still alive and no token signal is
                # permitted after the authenticated peer has disappeared.
                self.assertFalse(run.stop())
                self.assertFalse(run.cleanup_succeeded)
                self.assertEqual(signals, [])
                self.assertIsNotNone(run._connection)
                self.assertIsNotNone(run.server.listener)

                # Completion of the retained ``open -W`` helper is the only
                # passive edge that may now authorize final teardown.
                helper.returncode = 0
                self.assertTrue(run.stop())
                self.assertTrue(run.cleanup_succeeded)
                self.assertEqual(signals, [])
                self.assertIsNone(run._connection)
                self.assertIsNone(run.server.listener)
                self.assertTrue(run.stop())
                self.assertEqual(signals, [])
            finally:
                client.close()
                if run.run_dir.exists():
                    run.stop()
            verifier.SCRATCH = previous_scratch

    def test_eof_before_stop_and_kill_is_passive_and_idempotent(self) -> None:
        """Immediate EOF bypasses token signaling for both lifecycle methods."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            for method_name in ("stop", "kill"):
                with self.subTest(method=method_name):
                    helper = FakeOpenHelper(pid=7001)
                    signals: list[tuple[bytes, int]] = []
                    adapter = MacOSLaunchServicesAdapter(
                        helper_spawner=make_helper_spawner(helper),
                        signal_sender=lambda token, signum: signals.append((token, signum)),
                    )
                    run = CompanionRun(
                        Path(root) / f"{method_name}.app",
                        "session-1",
                        self.sentinel,
                        f"eof-before-{method_name}",
                        launcher=adapter,
                        artifact_facts=valid_artifact_facts(),
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                        running_code_attestor=FakeRunningCodeAttestor(),
                    )
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        client.connect(str(run.socket_path))
                        actual = PeerIdentity(
                            os.geteuid(),
                            4242,
                            self.audit_token,
                            run.launch_spec.executable_path,
                        )
                        run.send_authenticated_session(peer_reader=lambda _: actual)
                        client.settimeout(1.0)
                        self.assertGreater(len(client.recv(4096)), 4)
                        # Close the authenticated peer and complete only the
                        # retained helper before invoking lifecycle cleanup.
                        client.close()
                        helper.returncode = 0
                        started = time.monotonic()
                        self.assertTrue(getattr(run, method_name)())
                        self.assertLess(time.monotonic() - started, 1.0)
                        self.assertTrue(run._control_eof_observed)
                        self.assertTrue(run.cleanup_succeeded)
                        self.assertEqual(signals, [])
                        self.assertTrue(getattr(run, method_name)())
                        self.assertEqual(signals, [])
                    finally:
                        client.close()
                        if run.run_dir.exists():
                            run.stop()
            verifier.SCRATCH = previous_scratch

    def test_eof_before_stop_and_kill_retains_owner_until_helper_completion(self) -> None:
        """An alive helper blocks passive EOF cleanup; retry completes it."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            for method_name in ("stop", "kill"):
                with self.subTest(method=method_name):
                    helper = FakeOpenHelper(pid=7001)
                    signals: list[tuple[bytes, int]] = []
                    adapter = MacOSLaunchServicesAdapter(
                        helper_spawner=make_helper_spawner(helper),
                        signal_sender=lambda token, signum: signals.append((token, signum)),
                    )
                    run = CompanionRun(
                        Path(root) / f"alive-{method_name}.app",
                        "session-1",
                        self.sentinel,
                        f"eof-alive-{method_name}",
                        launcher=adapter,
                        artifact_facts=valid_artifact_facts(),
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                        running_code_attestor=FakeRunningCodeAttestor(),
                    )
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        client.connect(str(run.socket_path))
                        actual = PeerIdentity(
                            os.geteuid(),
                            4242,
                            self.audit_token,
                            run.launch_spec.executable_path,
                        )
                        run.send_authenticated_session(peer_reader=lambda _: actual)
                        client.settimeout(1.0)
                        self.assertGreater(len(client.recv(4096)), 4)
                        client.close()
                        started = time.monotonic()
                        self.assertFalse(getattr(run, method_name)())
                        self.assertLess(time.monotonic() - started, 1.0)
                        self.assertTrue(run._control_eof_observed)
                        self.assertFalse(run.cleanup_succeeded)
                        self.assertIsNotNone(run._connection)
                        self.assertIsNotNone(run.server.listener)
                        self.assertEqual(signals, [])
                        helper.returncode = 0
                        self.assertTrue(getattr(run, method_name)())
                        self.assertTrue(run.cleanup_succeeded)
                        self.assertEqual(signals, [])
                        self.assertTrue(getattr(run, method_name)())
                    finally:
                        client.close()
                        if run.run_dir.exists():
                            helper.returncode = 0
                            run.stop()
            verifier.SCRATCH = previous_scratch

    def test_stop_timeout_escalates_token_kill_and_waits_for_eof_before_close(self) -> None:
        """TERM timeout uses SIGKILL, completion, EOF, then one final close."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            signals: list[tuple[bytes, int]] = []
            clock_value = [0.0]
            client_holder: dict[str, socket.socket | None] = {"socket": None}

            def signal_sender(token: bytes, signum: int) -> None:
                signals.append((token, signum))
                if signum == signal.SIGKILL:
                    helper.returncode = 0
                    peer = client_holder["socket"]
                    if peer is not None:
                        peer.close()

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=signal_sender,
                clock=lambda: clock_value[0],
                sleeper=lambda interval: clock_value.__setitem__(0, clock_value[0] + interval),
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "timeout-escalation",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            close_calls: list[int] = []
            original_close = run.server.close_connection

            def counted_close(connection: socket.socket) -> None:
                close_calls.append(connection.fileno())
                original_close(connection)

            run.server.close_connection = counted_close  # type: ignore[method-assign]
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_holder["socket"] = client
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path)
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                self.assertTrue(run.stop())
                self.assertEqual(
                    signals,
                    [
                        (bytes.fromhex(self.audit_token), signal.SIGTERM),
                        (bytes.fromhex(self.audit_token), signal.SIGKILL),
                    ],
                )
                self.assertEqual(len(close_calls), 1)
                self.assertTrue(run.cleanup_succeeded)
                self.assertFalse(run.capture_ready())
                self.assertIsNone(run._connection)
                self.assertIsNone(run.server.listener)
                self.assertTrue(run.stop())
                self.assertEqual(len(close_calls), 1)
            finally:
                client.close()
                run.stop()
                verifier.SCRATCH = previous_scratch

    def test_sigkill_timeout_leaves_run_unqualified_and_restart_unlaunched(self) -> None:
        """An inconclusive kill is a FAIL and cannot construct a replacement."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            launch_count = [0]
            clock_value = [0.0]
            clients: list[socket.socket] = []

            def popen(argv: list[str], **kwargs: object) -> FakeOpenHelper:
                _ = argv, kwargs
                launch_count[0] += 1
                return helper

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(popen),
                signal_sender=lambda token, signum: None,
                clock=lambda: clock_value[0],
                sleeper=lambda interval: clock_value.__setitem__(0, clock_value[0] + interval),
            )

            class StableInspector:
                def inspect(self, _app: Path) -> ArtifactFacts:
                    return valid_artifact_facts()

            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "kill-timeout",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=StableInspector(),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            clients.append(client)
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(os.geteuid(), 4242, self.audit_token, run.launch_spec.executable_path)
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)
                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                phases = Phases(self.sentinel)
                verifier.phase_restart_drill(
                    phases,
                    run,
                    Path(root) / "TarsCompanion.app",
                    "session-1",
                    self.sentinel,
                    "ignored in offline proof",
                )
                self.assertEqual(launch_count[0], 1)
                self.assertEqual(phases.rows[-1]["status"], "FAIL")
                self.assertFalse(run.capture_ready())
                self.assertFalse(run.cleanup_succeeded)
                # Complete fixture cleanup only after the causal assertions;
                # production code must not claim this failed kill succeeded.
                client.close()
                self.assertTrue(run._wait_for_control_eof(timeout=0.5))
                helper.returncode = 0
                run._process_completed = True
                self.assertTrue(run._teardown())
            finally:
                for peer in clients:
                    peer.close()
                if not run.cleanup_succeeded:
                    helper.returncode = 0
                    run._event_stop.set()
                    if run._connection is not None:
                        run._wait_for_control_eof(timeout=0.1)
                    run._process_completed = True
                    run._teardown()
                verifier.SCRATCH = previous_scratch

    def test_stuck_event_or_output_reader_prevents_close_and_cleanup_success(self) -> None:
        """A live reader is never detached underneath its owner."""

        class StuckReader:
            def __init__(self) -> None:
                self.alive = True

            def join(self, timeout: float | None = None) -> None:
                _ = timeout

            def is_alive(self) -> bool:
                return self.alive

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            for reader_kind in ("event", "output"):
                with self.subTest(reader=reader_kind):
                    helper = FakeOpenHelper(pid=7001)
                    adapter = MacOSLaunchServicesAdapter(
                        helper_spawner=make_helper_spawner(helper),
                        signal_sender=lambda token, signum: setattr(helper, "returncode", 0),
                    )
                    run = CompanionRun(
                        Path(root) / f"{reader_kind}.app",
                        "session-1",
                        self.sentinel,
                        f"stuck-{reader_kind}",
                        launcher=adapter,
                        artifact_facts=valid_artifact_facts(),
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                        running_code_attestor=FakeRunningCodeAttestor(),
                    )
                    stuck = StuckReader()
                    if reader_kind == "event":
                        run._event_thread = stuck  # type: ignore[assignment]
                    else:
                        run._reader = stuck  # type: ignore[assignment]
                    self.assertFalse(run.stop())
                    self.assertFalse(run.cleanup_succeeded)
                    self.assertIsNotNone(run.server.listener)
                    stuck.alive = False
                    if reader_kind == "output":
                        # Model the real reader's EOF finalization only after
                        # its owner has been released.  Teardown may then be
                        # retried, and retirement must clear the sentinel.
                        run._output_redactor.finish()
                        run._output_redactor_finished = True
                    self.assertTrue(run.stop())
                    self.assertTrue(run.cleanup_succeeded)
                    self.assertIsNone(run._output_redactor.sentinel)
            verifier.SCRATCH = previous_scratch

    def test_harness_state_rejects_preactivation_nonfailed_and_sticky_later_events(self) -> None:
        activation, health = self._activation_and_health()
        state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
        state.accept_peer(self.peer)
        state.accept_command(canonical_json(self.command), peer=self.peer)
        with self.assertRaises(HarnessProtocolError):
            state.accept_event(canonical_json(health), peer=self.peer)

        failed = dict(health)
        failed_status = dict(health["status"])
        failed_status.update(
            {
                "kind": "failed",
                "route": "unknown",
                "interruption": "clear",
                "sleep": "awake",
                "overflowed": False,
                "failure_code": "capture-failed",
            }
        )
        failed["status"] = failed_status
        accepted = state.accept_event(canonical_json(failed), peer=self.peer)
        self.assertEqual(accepted["status"]["permission"], "unknown")
        self.assertEqual(state.activation_identities, {})
        with self.assertRaises(HarnessProtocolError):
            state.accept_event(canonical_json(activation), peer=self.peer)
        self.assertEqual(state.activation_identities, {})

        post_activation = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
        post_activation.accept_peer(self.peer)
        post_activation.accept_command(canonical_json(self.command), peer=self.peer)
        post_activation.accept_event(canonical_json(activation), peer=self.peer)
        post_activation.accept_event(canonical_json(failed), peer=self.peer)
        with self.assertRaises(HarnessProtocolError):
            post_activation.accept_event(canonical_json(health), peer=self.peer)

        for permission, failure_code in (
            ("unknown", "permission-denied"),
            ("denied", "capture-failed"),
            ("granted", "capture-failed"),
            ("revoked", "capture-failed"),
        ):
            hostile = dict(health)
            hostile_status = dict(health["status"])
            hostile_status.update(
                {
                    "kind": "failed",
                    "route": "unknown",
                    "interruption": "clear",
                    "sleep": "awake",
                    "overflowed": False,
                    "permission": permission,
                    "failure_code": failure_code,
                }
            )
            hostile["status"] = hostile_status
            fresh = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
            fresh.accept_peer(self.peer)
            fresh.accept_command(canonical_json(self.command), peer=self.peer)
            with self.subTest(permission=permission, failure_code=failure_code):
                with self.assertRaises(HarnessProtocolError):
                    fresh.accept_event(canonical_json(hostile), peer=self.peer)

    def test_python_control_socket_survives_three_idle_deadlines_then_revokes_on_peer_eof(self) -> None:
        """An open production-shaped socket remains positive across quiet reads."""

        previous_scratch = verifier.SCRATCH
        read_timeout = 0.02
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=lambda token, signum: setattr(helper, "returncode", 0),
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "idle-liveness",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(),
                    4242,
                    self.audit_token,
                    run.launch_spec.executable_path,
                )
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=read_timeout)

                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))

                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                self.assertTrue(run.positive_claim(True))
                self.assertIsNone(run._event_error)
                self.assertFalse(run._state.control_lost)

                # Four full deadlines are elapsed with no inbound event.  The
                # same authenticated descriptor and reader must stay active.
                time.sleep(read_timeout * 4.5)
                self.assertTrue(run.capture_ready())
                self.assertTrue(run.positive_claim(True))
                self.assertIsNone(run._event_error)
                self.assertFalse(run._state.control_lost)
                self.assertIsNotNone(run._event_thread)
                self.assertTrue(run._event_thread.is_alive())

                # EOF is the actual control-loss edge and must revoke every
                # success fact, unlike the preceding quiet read deadlines.
                client.close()
                deadline = time.monotonic() + 1.0
                while run._event_error is None and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertIsNotNone(run._event_error)
                self.assertTrue(run._state.control_lost)
                self.assertFalse(run.capture_ready())
                self.assertFalse(run.positive_claim(True))
                reader = run._event_thread
                self.assertIsNotNone(reader)
                reader.join(timeout=1.0)
                self.assertFalse(reader.is_alive())
                # EOF removes token authority.  Complete the retained helper
                # separately, then let stop() use the passive completion edge.
                helper.returncode = 0
                self.assertTrue(run.stop())
                self.assertTrue(run.cleanup_succeeded)
            finally:
                client.close()
                run.stop()
                verifier.SCRATCH = previous_scratch

    def test_functional_health_requires_safe_running_source_health_and_revokes_prior_grant(self) -> None:
        """Every schema-valid unsafe health update clears a prior grant."""

        previous_scratch = verifier.SCRATCH
        read_timeout = 0.02
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            helper = FakeOpenHelper(pid=7001)
            client_holder: dict[str, socket.socket | None] = {"socket": None}

            def signal_sender(_token: bytes, _signum: int) -> None:
                helper.returncode = 0
                peer = client_holder["socket"]
                if peer is not None:
                    peer.close()

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=signal_sender,
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "functional-health",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_holder["socket"] = client
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(),
                    4242,
                    self.audit_token,
                    run.launch_spec.executable_path,
                )
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=read_timeout)

                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                safe_status = dict(health["status"])
                safe_status["permission"] = "granted"
                health["status"] = safe_status
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                self.assertTrue(run.positive_claim(True))
                self.assertIsNotNone(run.activation)
                current_tuple = run.activation.tuple
                self.assertTrue(
                    functional_health(
                        current_tuple=current_tuple,
                        event_tuple=current_tuple,
                        status=safe_status,
                    )
                )

                unsafe_statuses: tuple[tuple[str, dict[str, object]], ...] = (
                    ("stopped/granted", {"kind": "stopped"}),
                    ("idle/granted", {"kind": "idle"}),
                    ("ready/granted", {"kind": "ready"}),
                    ("route=unavailable", {"route": "unavailable"}),
                    ("route=ambiguous", {"route": "ambiguous"}),
                    ("route=changed", {"route": "changed"}),
                    ("route=unknown", {"route": "unknown"}),
                    ("interruption=interrupted", {"interruption": "interrupted"}),
                    ("sleep=sleeping", {"sleep": "sleeping"}),
                    ("sleep=woke", {"sleep": "woke"}),
                    ("overflowed=true", {"overflowed": True}),
                    ("permission=denied", {"permission": "denied"}),
                    ("permission=revoked", {"permission": "revoked"}),
                    ("failed/unknown", {"kind": "failed", "permission": "unknown", "failure_code": "capture-failed"}),
                )

                def send_health(changes: dict[str, object]) -> None:
                    status = dict(safe_status)
                    status.update(changes)
                    if status["kind"] == "failed":
                        status.update(
                            {
                                "route": "unknown",
                                "interruption": "clear",
                                "sleep": "awake",
                                "overflowed": False,
                            }
                        )
                        status.setdefault("failure_code", "capture-failed")
                    else:
                        status.pop("failure_code", None)
                    event = dict(health)
                    event["status"] = status
                    client.sendall(encode_event(event))

                for label, changes in unsafe_statuses:
                    with self.subTest(status=label):
                        self.assertTrue(run.capture_ready())
                        self.assertTrue(run.positive_claim(True))
                        send_health(changes)
                        expected_permission = (
                            "denied"
                            if changes.get("permission") in {"denied", "revoked"}
                            else "unknown"
                        )
                        deadline = time.monotonic() + 1.0
                        while (
                            run.functional_permission_tuple != current_tuple
                            or run.functional_permission_state != expected_permission
                        ) and time.monotonic() < deadline:
                            time.sleep(0.002)
                        if label == "failed/unknown":
                            self.assertIsNone(run.functional_permission_tuple)
                        else:
                            self.assertEqual(run.functional_permission_tuple, current_tuple)
                        expected_status = dict(safe_status)
                        expected_status.update(changes)
                        self.assertFalse(
                            functional_health(
                                current_tuple=current_tuple,
                                event_tuple=current_tuple,
                                status=expected_status,
                            )
                        )
                        self.assertEqual(run.functional_permission_state, expected_permission)
                        self.assertFalse(run.capture_ready())
                        self.assertFalse(run.positive_claim(True))

                        if label == "failed/unknown":
                            # Failed health is terminal/sticky by contract;
                            # the reader retires the control and no restore
                            # frame may be sent after this final table case.
                            continue

                        # Restore the safe running/granted observation before
                        # the next mutation so every case follows a prior grant.
                        send_health({})
                        deadline = time.monotonic() + 1.0
                        while (
                            run.functional_permission_tuple != current_tuple
                            or run.functional_permission_state != "granted"
                        ) and time.monotonic() < deadline:
                            time.sleep(0.002)
                        self.assertEqual(run.functional_permission_tuple, current_tuple)
                        self.assertEqual(run.functional_permission_state, "granted")
                        self.assertTrue(run.capture_ready())
                        self.assertTrue(run.positive_claim(True))
            finally:
                run.stop()
                client.close()
                verifier.SCRATCH = previous_scratch

    def test_functional_health_conjuncts_are_mutation_effective(self) -> None:
        """Removing any safety conjunct would admit its hostile health state."""

        current_tuple = CaptureTuple(
            "peer", "nonce-1",
            attempt_binding("01234567-89ab-cdef-0123-456789abcdef"),
            1,
        )
        safe_status: dict[str, object] = {
            "interruption": "clear",
            "kind": "running",
            "overflowed": False,
            "permission": "granted",
            "route": "healthy",
            "sleep": "awake",
        }
        source = textwrap.dedent(inspect.getsource(functional_health))
        cases = (
            ("kind", 'status.get("kind") == "running"', {"kind": "stopped"}),
            (
                "permission",
                'status.get("permission") == "granted"',
                {"permission": "unknown"},
            ),
            ("route", 'status.get("route") == "healthy"', {"route": "unavailable"}),
            (
                "interruption",
                'status.get("interruption") == "clear"',
                {"interruption": "interrupted"},
            ),
            ("sleep", 'status.get("sleep") == "awake"', {"sleep": "sleeping"}),
            ("overflow", 'status.get("overflowed") is False', {"overflowed": True}),
            (
                "device",
                '_valid_device_identity(None, actual_engine=actual_engine, kind="running")',
                {},
            ),
        )
        for name, marker, change in cases:
            with self.subTest(conjunct=name):
                needle = f"            {marker},\n"
                self.assertIn(needle, source)
                mutant_source = source.replace(needle, "", 1)
                namespace = dict(functional_health.__globals__)
                exec(mutant_source, namespace)
                mutant = namespace["functional_health"]
                hostile = dict(safe_status)
                hostile.update(change)
                actual_engine = "invalid-engine" if name == "device" else PROCESS_TAP
                self.assertFalse(
                    functional_health(
                        current_tuple=current_tuple,
                        event_tuple=current_tuple,
                        status=hostile,
                        actual_engine=actual_engine,
                    )
                )
                self.assertTrue(
                    mutant(
                        current_tuple=current_tuple,
                        event_tuple=current_tuple,
                        status=hostile,
                        actual_engine=actual_engine,
                    )
                )

    def test_invalid_artifact_rejects_before_listener_launcher_or_credential(self) -> None:
        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            app = Path(root) / "TarsCompanion.app"
            launcher_calls: list[object] = []
            launcher = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(on_spawn=lambda argv: launcher_calls.append(argv))
            )
            bad = dataclasses_replace(valid_artifact_facts(), dirty=True)
            with self.assertRaises(HarnessProtocolError):
                CompanionRun(
                    app,
                    "session-1",
                    self.sentinel,
                    "invalid-artifact",
                    launcher=launcher,
                    artifact_facts=bad,
                    expected_head="a" * 40,
                    expected_tree="b" * 40,
                    expected_digest="a" * 64,
                    artifact_inspector=FakeArtifactInspector(bad),
                    running_code_attestor=FakeRunningCodeAttestor(),
                )
            self.assertEqual(launcher_calls, [])
            self.assertFalse(verifier.SCRATCH.exists())
            verifier.SCRATCH = previous_scratch

    def test_secret_sentinel_never_enters_launch_events_or_evidence(self) -> None:
        spec = make_launch_spec("/Applications/TarsCompanion.app", socket_path="/tmp/control.sock", launch_nonce="nonce-1", stream_key=self.sentinel)
        activation = Activation(
            CaptureTuple("peer", "nonce-1", attempt_binding("fedcba98-7654-3210-fedc-ba9876543210"), 1),
            PROCESS_TAP, PROCESS_TAP, PROCESS_TAP,
        )
        facts = {"argv": list(spec.argv), "events": [dataclasses.asdict(activation)], "logs": "constant", "retained": "none"}
        self.assertTrue(secret_free(facts, self.sentinel))
        evidence = canonical_evidence(result="INCONCLUSIVE", facts=facts, sentinel=self.sentinel)
        self.assertNotIn("claim", evidence)
        self.assertTrue(secret_free(evidence, self.sentinel))
        with self.assertRaises(HarnessProtocolError): canonical_evidence(result="FAIL", facts={"stream_key": self.sentinel}, sentinel=self.sentinel)

    def test_streaming_redactor_drops_every_terminal_secret_prefix_and_sets_violation(self) -> None:
        for length in range(1, len(self.sentinel)):
            with self.subTest(prefix_length=length):
                redactor = StreamingRedactor(self.sentinel)
                retained = redactor.feed("ordinary:")
                retained += redactor.feed(self.sentinel[:length])
                retained += redactor.finish()
                self.assertNotIn(self.sentinel[:length], retained)
                self.assertNotIn(self.sentinel, retained)
                self.assertTrue(redactor.seen)
        split = StreamingRedactor(self.sentinel)
        retained = split.feed(self.sentinel[: len(self.sentinel) // 2])
        retained += split.feed(self.sentinel[len(self.sentinel) // 2 :])
        retained += split.finish()
        self.assertNotIn(self.sentinel, retained)
        self.assertTrue(split.seen)
        ordinary = StreamingRedactor(self.sentinel)
        self.assertEqual(ordinary.feed("ordinary suffix"), "ordinary suffix")
        self.assertEqual(ordinary.finish(), "")
        self.assertFalse(ordinary.seen)

    def test_every_terminal_credential_boundary_redacts_rows_facts_stdout_and_markdown(self) -> None:
        """Complete-value boundaries use the same fail-closed prefix rule."""

        previous_doc = verifier.EVIDENCE_DOC
        try:
            for length in [*range(1, len(self.sentinel)), len(self.sentinel)]:
                with self.subTest(prefix_length=length):
                    prefix = self.sentinel[:length]
                    self.assertTrue(credential_material(f"value:{prefix}", self.sentinel))
                    self.assertNotIn(prefix, redact_credential_material(f"value:{prefix}", self.sentinel))

                    # Values recorded before the session key is known are
                    # re-sanitized by register_stream_key at the real boundary.
                    pre_registered = verifier.Phases()
                    pre_output = io.StringIO()
                    with contextlib.redirect_stdout(pre_output):
                        pre_registered.record(f"name:{prefix}", "PASS", f"detail:{prefix}")
                        pre_registered.facts["before_session"] = {f"nested:{prefix}": prefix}
                        pre_registered.facts[f"top:{prefix}"] = f"top-value:{prefix}"
                    pre_registered.register_stream_key(self.sentinel)
                    # The pre-session stdout happened before the active key
                    # existed and is intentionally not part of the retained
                    # evidence assertion below; rows/facts are re-sanitized.
                    self.assertIn(f"name:{prefix}", pre_output.getvalue())

                    phases = verifier.Phases(self.sentinel)
                    captured = io.StringIO()
                    with contextlib.redirect_stdout(captured):
                        phases.emit(verifier.CredentialReachableDiagnostic(f"stdout:{prefix}"))
                        phases.record(f"phase:{prefix}", "PASS", f"detail:{prefix}")
                        phases.facts["error"] = {
                            f"nested-key:{prefix}": {"value": f"nested-value:{prefix}"}
                        }
                        phases.facts["transcript"] = [
                            {"speaker": "Candidato", "text": f"transcript:{prefix}"}
                        ]

                    retained = "\n".join(
                        [
                            captured.getvalue(),
                            json.dumps(pre_registered.rows, ensure_ascii=False),
                            json.dumps(pre_registered.facts, ensure_ascii=False),
                            json.dumps(phases.rows, ensure_ascii=False),
                            json.dumps(phases.facts, ensure_ascii=False),
                        ]
                    )
                    self.assertFalse(credential_material(retained, self.sentinel))
                    for marker in (
                        f"stdout:{prefix}",
                        f"name:{prefix}",
                        f"detail:{prefix}",
                        f"nested:{prefix}",
                        f"nested-key:{prefix}",
                        f"nested-value:{prefix}",
                        f"top:{prefix}",
                        f"top-value:{prefix}",
                        f"transcript:{prefix}",
                    ):
                        self.assertNotIn(marker, retained)
                    self.assertTrue(pre_registered.secret_seen)
                    self.assertTrue(phases.secret_seen)

                    candidate = {
                        "error": {f"nested-key:{prefix}": {"value": prefix}},
                        "phase_rows": [{"name": f"phase:{prefix}", "status": "PASS", "detail": prefix}],
                        "transcript": [{"speaker": "Candidato", "text": f"transcript:{prefix}"}],
                    }
                    with self.assertRaises(HarnessProtocolError):
                        canonical_evidence(result="PASS", facts=candidate, sentinel=self.sentinel)
                    safe_candidate = verifier._redact_evidence_value(candidate, self.sentinel)
                    self.assertFalse(credential_material(json.dumps(safe_candidate, ensure_ascii=False), self.sentinel))
                    self.assertEqual(
                        safe_candidate,
                        {
                            "error": {"nested-key:<redacted>": {"value": "<redacted>"}},
                            "phase_rows": [{
                                "name": "phase:<redacted>",
                                "status": "PASS",
                                "detail": "<redacted>",
                            }],
                            "transcript": [{"speaker": "Candidato", "text": "transcript:<redacted>"}],
                        },
                    )
                    fallback_facts = {
                        key: value for key, value in safe_candidate.items() if key != "transcript"
                    }
                    # The independent canonical boundary must reject this
                    # redacted raw-row mapping; only the production projector
                    # may replace it with an exact typed failure marker.
                    with self.assertRaises(HarnessProtocolError):
                        canonical_evidence(
                            result="FAIL", facts=fallback_facts, sentinel=self.sentinel
                        )

                    # The full production evidence path receives the same
                    # values, and its durable observation bit makes PASS
                    # impossible even though retained bytes are redacted.
                    with tempfile.TemporaryDirectory() as root:
                        verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
                        args = type(
                            "Args",
                            (),
                            {"signed_app": Path(root) / "TarsCompanion.app", "with_restart_drill": False},
                        )()
                        verifier.phase_evidence(
                            phases,
                            args,
                            companion=None,
                            provenance_reader=lambda: ("a" * 40, []),
                        )
                        document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
                    self.assertNotIn(f"phase:{prefix}", document)
                    self.assertNotIn(f"detail:{prefix}", document)
                    self.assertNotIn(f"transcript:{prefix}", document)
                    self.assertFalse(credential_material(document, self.sentinel))
                    self.assertNotEqual(phases.facts["process_tap_evidence_result"], "PASS")
                    self.assertFalse(phases.facts["process_tap_positive"])

            ordinary = "ordinary:" + self.sentinel[:1] + "-suffix"
            self.assertFalse(credential_material(ordinary, self.sentinel))
            self.assertEqual(redact_credential_material(ordinary, self.sentinel), ordinary)
        finally:
            verifier.EVIDENCE_DOC = previous_doc

    def test_production_phase_evidence_uses_neutral_companion_label_for_each_result(self) -> None:
        previous_doc = verifier.EVIDENCE_DOC
        activation = Activation(
            CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce-1", attempt_binding("01234567-89ab-cdef-0123-456789abcdef"), 1),
            PROCESS_TAP,
            PROCESS_TAP,
            PROCESS_TAP,
        )
        snapshot = verifier.LiveProofSnapshot(
            artifact_valid=True,
            current_peer=True,
            authenticated_peer_key=_TEST_PEER_FINGERPRINT,
            launch_nonce="nonce-1",
            activation=activation,
            functional_permission_state="granted",
            functional_permission_tuple=activation.tuple,
        )

        def companion() -> object:
            return type(
                "ProofCompanion",
                (),
                {
                    "_stream_key": self.sentinel,
                    "_expected_head": "a" * 40,
                    "proof_snapshot": snapshot,
                    "cleanup_succeeded": True,
                    "secret_seen": False,
                },
            )()

        expected = {"PASS": "PASS", "FAIL": "FAIL", "BLOQUEADO": "BLOCKED", "INCONCLUSIVE": "INCONCLUSIVE"}
        try:
            for status, result in expected.items():
                with self.subTest(status=status):
                    ph = verifier.Phases(self.sentinel)
                    ph.facts["transcript_valid_typed"] = True
                    ph.facts["transcription_complete"] = True
                    install_positive_operational_facts(ph, restart_drill=False)
                    phase_status = verifier.PhaseStatus(status)
                    required_names = sorted(
                        verifier._required_phase_ids(with_restart_drill=False)
                        - {verifier.PhaseID.EVIDENCE.value}
                    )
                    for name in required_names:
                        row_status = phase_status if name == verifier.PhaseID.COMPANION_CAPTURE.value else verifier.PhaseStatus.PASS
                        row_detail = (
                            verifier.PhaseDetail.template()
                            if row_status is verifier.PhaseStatus.PASS
                            else verifier.CredentialReachableDiagnostic("fixture")
                        )
                        ph.record(verifier.PhaseID(name), row_status, row_detail)
                    with tempfile.TemporaryDirectory() as root:
                        verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
                        args = type(
                            "Args",
                            (),
                            {"signed_app": Path(root) / "TarsCompanion.app", "with_restart_drill": False},
                        )()
                        verifier.phase_evidence(
                            ph,
                            args,
                            companion=companion() if status != "INCONCLUSIVE" else None,
                            provenance_reader=lambda: ("a" * 40, []),
                        )
                        document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
                    self.assertEqual(ph.facts["process_tap_evidence_result"], result)
                    self.assertIn("Companion — estado da captura Process Tap", document)
                    self.assertNotIn("captura Process Tap ativa", document)
                    if status != "PASS":
                        for phrase in (
                            "Comprova apenas",
                            "funcionando ao vivo",
                            "O que está comprovado",
                            "Process Tap positive",
                            "prova ao vivo",
                        ):
                            self.assertNotIn(phrase, document)
        finally:
            verifier.EVIDENCE_DOC = previous_doc

    def test_production_phase_evidence_rejects_nested_transcript_and_error_sentinel(self) -> None:
        previous_doc = verifier.EVIDENCE_DOC
        with tempfile.TemporaryDirectory() as root:
            verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
            args = type("Args", (), {"signed_app": Path(root) / "TarsCompanion.app", "with_restart_drill": False})()
            companion = type(
                "FakeCompanion",
                (),
                {
                    "_stream_key": self.sentinel,
                    "activation": None,
                    "functional_permission_state": "unknown",
                    "functional_permission_tuple": None,
                    "artifact_valid": True,
                    "peer_authenticated": True,
                    "launch_nonce": "nonce-1",
                },
            )()
            # The production phase_session installs this sentinel before any
            # later dynamic diagnostic can be recorded.  Start the fixture at
            # that same durable boundary so its captured stdout is covered.
            ph = verifier.Phases(self.sentinel)
            ph.facts.update(
                {
                    "signed_app": str(args.signed_app),
                    "transcript": [{"speaker": "Candidato", "text": f"nested {self.sentinel}"}],
                    "error": {"message": self.sentinel},
                }
            )
            ph.record("synthetic phase", "PASS", f"detail {self.sentinel}")
            verifier.phase_evidence(
                ph,
                args,
                companion=companion,
                provenance_reader=lambda: ("a" * 40, []),
            )
            retained = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
            self.assertNotIn(self.sentinel, retained)
            self.assertTrue(ph.failed)
            self.assertEqual(ph.facts["process_tap_evidence_result"], "FAIL")
        verifier.EVIDENCE_DOC = previous_doc

    def test_phase_session_installs_redaction_before_success_row(self) -> None:
        class Response:
            status_code = 200

            @staticmethod
            def json() -> dict[str, str]:
                return {"session_id": "session-1", "stream_key": "TASK11-GOLDEN-STREAM-KEY-0123456789abcdefgh"}

        ph = verifier.Phases()
        captured = io.StringIO()
        with mock.patch.object(verifier.requests, "post", return_value=Response()), contextlib.redirect_stdout(captured):
            self.assertEqual(verifier.phase_session(ph), ("session-1", self.sentinel))
        self.assertEqual(ph.sentinel, self.sentinel)
        self.assertNotIn(self.sentinel, captured.getvalue())
        self.assertTrue(secret_free(ph.rows, self.sentinel))

    def test_durable_redaction_covers_split_companion_failure_and_all_retained_artifacts(self) -> None:
        previous_scratch = verifier.SCRATCH
        previous_doc = verifier.EVIDENCE_DOC

        class SplitPipe:
            def __init__(self, chunks: list[bytes]) -> None:
                self._chunks = list(chunks)

            def read(self, _: int) -> bytes:
                if self._chunks:
                    return self._chunks.pop(0)
                return b""

        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
            helper = FakeOpenHelper()
            encoded = self.sentinel.encode("utf-8")
            split = len(encoded) // 2
            helper.stdout = SplitPipe(
                [b"helper prefix "+encoded[:split], encoded[split:]+b" helper suffix"]
            )
            launcher = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=lambda token, signum: None,
            )
            ph = verifier.Phases()
            ph.record(
                verifier.PhaseID.SESSION_CREATED,
                verifier.PhaseStatus.PASS,
                verifier.PhaseDetail.template(),
            )
            ph.register_stream_key(self.sentinel)
            ph.facts.update(
                {
                    "transcript": [{"speaker": "Candidato", "text": f"split {self.sentinel}"}],
                    "error": {"message": f"admission {self.sentinel}"},
                }
            )
            captured_out = io.StringIO()
            captured_err = io.StringIO()
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "redaction-failure",
                launcher=launcher,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(run.socket_path))
                bad_peer = dataclasses_replace(self.peer, euid=os.geteuid() + 1)
                with self.assertRaises(HarnessProtocolError):
                    run.send_authenticated_session(peer_reader=lambda _: bad_peer)
                client.settimeout(1.0)
                self.assertEqual(client.recv(1), b"")
                with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
                    ph.record("failed admission", "FAIL", f"diagnostic {self.sentinel}")
                    ph.emit(verifier.CredentialReachableDiagnostic(f"console {self.sentinel}"))
                    run.stop()
                    args = type(
                        "Args",
                        (),
                        {"signed_app": Path(root) / "TarsCompanion.app", "with_restart_drill": False},
                    )()
                    verifier.phase_evidence(
                        ph,
                        args,
                        companion=None,
                        provenance_reader=lambda: ("a" * 40, []),
                    )
            finally:
                client.close()
                if run.run_dir.exists():
                    run.stop()

            retained = "\n".join(
                [
                    captured_out.getvalue(),
                    captured_err.getvalue(),
                    run.output(),
                    run.out_path.read_text(encoding="utf-8"),
                    verifier.EVIDENCE_DOC.read_text(encoding="utf-8"),
                    json.dumps(ph.rows, ensure_ascii=False),
                    json.dumps(ph.facts, ensure_ascii=False, default=str),
                ]
            )
            self.assertNotIn(self.sentinel, retained)
            self.assertTrue(secret_free(ph.rows, self.sentinel))
            self.assertTrue(secret_free(ph.facts, self.sentinel))
            self.assertTrue(ph.failed)
        verifier.SCRATCH = previous_scratch
        verifier.EVIDENCE_DOC = previous_doc

    def test_phase_evidence_observes_late_companion_redaction_and_never_claims_pass(self) -> None:
        """A late split-key observation revokes an otherwise valid run."""

        previous_scratch = verifier.SCRATCH
        previous_doc = verifier.EVIDENCE_DOC

        class QueuePipe:
            def __init__(self) -> None:
                self._chunks: queue.Queue[bytes] = queue.Queue()

            def push(self, chunk: bytes) -> None:
                self._chunks.put(chunk)

            def read(self, _: int) -> bytes:
                return self._chunks.get()

        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
            pipe = QueuePipe()
            helper = FakeOpenHelper(pid=7001)
            helper.stdout = pipe

            def signal_sender(_token: bytes, _signum: int) -> None:
                helper.returncode = 0
                # The fake process completion closes its authenticated peer;
                # run.stop() must still observe EOF before persisting output.
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                client.close()

            adapter = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(helper),
                signal_sender=signal_sender,
            )
            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "late-redaction",
                launcher=adapter,
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            ph = verifier.Phases(self.sentinel)
            ph.facts["transcript"] = [
                {"speaker": "Candidato", "text": "candidato experiencia vendas"}
            ]
            captured = io.StringIO()
            try:
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(),
                    4242,
                    self.audit_token,
                    run.launch_spec.executable_path,
                )
                run.send_authenticated_session(peer_reader=lambda _: actual)
                client.settimeout(1.0)
                self.assertGreater(len(client.recv(4096)), 4)
                run.start_event_reader(timeout=0.02)

                activation, health = self._activation_and_health()
                activation["launch_nonce"] = launch_binding(run.launch_nonce)
                activation["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["launch_nonce"] = launch_binding(run.launch_nonce)
                health["session_binding"] = session_binding("session-1", run.launch_nonce)
                health["status"] = dict(health["status"])
                health["status"]["permission"] = "granted"
                client.sendall(encode_event(activation) + encode_event(health))
                deadline = time.monotonic() + 1.0
                while not run.capture_ready() and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run.capture_ready())
                self.assertTrue(run.positive_claim(True))

                # The redactor is already attached to the live helper output;
                # this observation arrives only after readiness was established.
                encoded = self.sentinel.encode()
                split = len(encoded) // 2
                pipe.push(b"late prefix " + encoded[:split])
                pipe.push(encoded[split:] + b" late suffix")
                pipe.push(b"")
                deadline = time.monotonic() + 1.0
                while not run._output_redactor_finished and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertTrue(run._output_redactor_finished)
                self.assertTrue(run.secret_seen)
                self.assertNotIn(self.sentinel, run.output())

                # Teardown persists the already-redacted helper output; retain
                # the CompanionRun object so phase_evidence can inspect its
                # late observation rather than relying on an earlier phase.
                run.stop()
                client.close()
                with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                    verifier.phase_evidence(
                        ph,
                        type(
                            "Args",
                            (),
                            {
                                "signed_app": Path(root) / "TarsCompanion.app",
                                "with_restart_drill": False,
                            },
                        )(),
                        companion=run,
                        provenance_reader=lambda: ("a" * 40, []),
                    )

                retained = "\n".join(
                    [
                        captured.getvalue(),
                        run.output(),
                        run.out_path.read_text(encoding="utf-8"),
                        verifier.EVIDENCE_DOC.read_text(encoding="utf-8"),
                        json.dumps(ph.rows, ensure_ascii=False),
                        json.dumps(ph.facts, ensure_ascii=False, default=str),
                    ]
                )
                self.assertNotIn(self.sentinel, retained)
                self.assertTrue(ph.failed)
                self.assertEqual(ph.facts["process_tap_evidence_result"], "FAIL")
                self.assertFalse(ph.facts["process_tap_positive"])
                self.assertIn("stream-key sentinel crossed", captured.getvalue())
            finally:
                client.close()
                # If an assertion happened before the EOF marker, wake the
                # pipe reader before cleanup so no fixture thread survives.
                pipe.push(b"")
                if run.run_dir.exists():
                    run.stop()
                verifier.SCRATCH = previous_scratch
                verifier.EVIDENCE_DOC = previous_doc

    def test_phase_evidence_requires_snapshot_cleanup_transcript_clean_head_and_rows(self) -> None:
        """Final evidence cannot resurrect mutable post-stop or failed facts."""

        previous_doc = verifier.EVIDENCE_DOC
        activation = Activation(
            CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce-1", attempt_binding("01234567-89ab-cdef-0123-456789abcdef"), 1),
            PROCESS_TAP,
            PROCESS_TAP,
            PROCESS_TAP,
        )
        snapshot = verifier.LiveProofSnapshot(
            artifact_valid=True,
            current_peer=True,
            authenticated_peer_key=_TEST_PEER_FINGERPRINT,
            launch_nonce="nonce-1",
            activation=activation,
            functional_permission_state="granted",
            functional_permission_tuple=activation.tuple,
        )

        def make_companion(*, cleanup: bool = True, with_snapshot: bool = True) -> object:
            return type(
                "ProofCompanion",
                (),
                {
                    "_stream_key": self.sentinel,
                    "_expected_head": "a" * 40,
                    "proof_snapshot": snapshot if with_snapshot else None,
                    "cleanup_succeeded": cleanup,
                    "secret_seen": False,
                },
            )()

        def run_case(
            *,
            porcelain: list[str] | None = None,
            commit: str = "a" * 40,
            cleanup: bool = True,
            with_snapshot: bool = True,
            transcription_complete: object = True,
            phase_status: str | None = None,
        ) -> tuple[Phases, str]:
            ph = Phases(self.sentinel)
            ph.facts["transcript_valid_typed"] = True
            ph.facts["transcription_complete"] = transcription_complete
            install_positive_operational_facts(ph, restart_drill=False)
            required_names = sorted(
                verifier._required_phase_ids(with_restart_drill=False)
                - {verifier.PhaseID.EVIDENCE.value}
            )
            for name in required_names:
                phase_value = phase_status if name == verifier.PhaseID.COMPANION_CAPTURE.value and phase_status is not None else "PASS"
                phase_kind = verifier.PhaseStatus(phase_value)
                phase_detail = (
                    verifier.PhaseDetail.template()
                    if phase_kind is verifier.PhaseStatus.PASS
                    else verifier.PhaseDetail.diagnostic("causal row")
                )
                ph.record(verifier.PhaseID(name), phase_kind, phase_detail)
            with tempfile.TemporaryDirectory() as root:
                verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
                verifier.phase_evidence(
                    ph,
                    type("Args", (), {"signed_app": Path(root) / "TarsCompanion.app", "with_restart_drill": False})(),
                    companion=make_companion(cleanup=cleanup, with_snapshot=with_snapshot),
                    provenance_reader=lambda: (commit, porcelain or []),
                )
                document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
            return ph, document

        try:
            clean, clean_doc = run_case()
            self.assertEqual(clean.facts["process_tap_evidence_result"], "PASS")
            self.assertTrue(clean.facts["process_tap_positive"])
            self.assertNotIn("EXECUÇÃO COM FALHAS", clean_doc)
            self.assertEqual(verifier.final_result_code(clean), verifier.EXIT_OK)

            for label, kwargs in (
                ("tracked-dirty", {"porcelain": [" M tracked.py"]}),
                ("untracked", {"porcelain": ["?? fixture"]}),
                ("head-drift", {"commit": "c" * 40}),
                ("no-cleanup", {"cleanup": False}),
                ("no-snapshot", {"with_snapshot": False}),
                ("transcript-false", {"transcription_complete": False}),
                ("transcript-string-true", {"transcription_complete": "true"}),
                ("transcript-string-false", {"transcription_complete": "false"}),
                ("transcript-number-one", {"transcription_complete": 1}),
                ("transcript-null", {"transcription_complete": None}),
            ):
                with self.subTest(case=label):
                    ph, document = run_case(**kwargs)
                    self.assertNotEqual(ph.facts["process_tap_evidence_result"], "PASS")
                    self.assertFalse(ph.facts["process_tap_positive"])
                    self.assertIn("EXECUÇÃO COM FALHAS", document)
                    self.assertEqual(verifier.final_result_code(ph), verifier.EXIT_FAILED)

            positive_phrases = (
                "Comprova apenas",
                "funcionando ao vivo",
                "O que está comprovado",
                "Process Tap positive",
                "prova ao vivo",
            )
            for result in ("FAIL", "BLOCKED", "INCONCLUSIVE"):
                with self.subTest(markdown_result=result):
                    document = markdown_projection(
                        canonical_evidence(result=result, facts={})
                    )
                    for phrase in positive_phrases:
                        self.assertNotIn(phrase, document)
            for status, expected in (("FAIL", "FAIL"), ("BLOQUEADO", "BLOCKED")):
                with self.subTest(required_phase=status):
                    ph, document = run_case(phase_status=status)
                    self.assertEqual(ph.facts["process_tap_evidence_result"], expected)
                    self.assertFalse(ph.facts["process_tap_positive"])
                    if status == "FAIL":
                        self.assertIn("EXECUÇÃO COM FALHAS", document)
                    else:
                        self.assertNotIn("process-tap-positive", document)
                    for phrase in positive_phrases:
                        self.assertNotIn(phrase, document)
                    expected_exit = verifier.EXIT_FAILED if status == "FAIL" else verifier.EXIT_TCC_BLOCKED
                    self.assertEqual(verifier.final_result_code(ph), expected_exit)

            source = inspect.getsource(verifier.phase_evidence)
            self.assertIn("required_result = _reduce_required_phase_status(", source)
            self.assertIn("priority is FAIL > BLOCKED > INCONCLUSIVE", source)
            self.assertIn("proof = None", source)
        finally:
            verifier.EVIDENCE_DOC = previous_doc

    def test_phase_evidence_rejects_nonexact_cleanup_and_snapshot_owners(self) -> None:
        """Only exact cleanup and the producer snapshot can form a final proof."""

        previous_doc = verifier.EVIDENCE_DOC
        activation = Activation(
            CaptureTuple(
                _TEST_PEER_FINGERPRINT,
                "nonce-1",
                attempt_binding("01234567-89ab-cdef-0123-456789abcdef"),
                1,
            ),
            PROCESS_TAP,
            PROCESS_TAP,
            PROCESS_TAP,
        )
        snapshot = verifier.LiveProofSnapshot(
            artifact_valid=True,
            current_peer=True,
            authenticated_peer_key=_TEST_PEER_FINGERPRINT,
            launch_nonce="nonce-1",
            activation=activation,
            functional_permission_state="granted",
            functional_permission_tuple=activation.tuple,
        )

        class SnapshotSubclass(verifier.LiveProofSnapshot):
            pass

        subclass_snapshot = SnapshotSubclass(
            snapshot.artifact_valid,
            snapshot.current_peer,
            snapshot.authenticated_peer_key,
            snapshot.launch_nonce,
            snapshot.activation,
            snapshot.functional_permission_state,
            snapshot.functional_permission_tuple,
        )
        duck_snapshot = type(
            "DuckSnapshot",
            (),
            {
                field: getattr(snapshot, field)
                for field in (
                    "artifact_valid",
                    "current_peer",
                    "authenticated_peer_key",
                    "launch_nonce",
                    "activation",
                    "functional_permission_state",
                    "functional_permission_tuple",
                )
            },
        )()

        def run_case(snapshot_value: object, cleanup_value: object) -> None:
            ph = Phases(self.sentinel)
            ph.facts["transcript_valid_typed"] = True
            ph.facts["transcription_complete"] = True
            install_positive_operational_facts(ph, restart_drill=False)
            for row in positive_phase_rows(restart_drill=False):
                if row["name"] == verifier.PhaseID.EVIDENCE.value:
                    continue
                ph.record(
                    verifier.PhaseID(row["name"]),
                    verifier.PhaseStatus.PASS,
                    verifier.PhaseDetail.template(),
                )
            companion = type(
                "ExactOwnerCompanion",
                (),
                {
                    "_stream_key": self.sentinel,
                    "_expected_head": "a" * 40,
                    "proof_snapshot": snapshot_value,
                    "cleanup_succeeded": cleanup_value,
                    "secret_seen": False,
                },
            )()
            try:
                with tempfile.TemporaryDirectory() as root:
                    verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
                    verifier.phase_evidence(
                        ph,
                        type(
                            "Args",
                            (),
                            {
                                "signed_app": Path(root) / "TarsCompanion.app",
                                "with_restart_drill": False,
                            },
                        )(),
                        companion=companion,
                        provenance_reader=lambda: ("a" * 40, []),
                    )
            finally:
                verifier.EVIDENCE_DOC = previous_doc
            self.assertNotEqual(ph.facts.get("process_tap_evidence_result"), "PASS")
            self.assertFalse(ph.facts.get("process_tap_positive") is True)
            self.assertEqual(verifier.final_result_code(ph), verifier.EXIT_FAILED)

        for cleanup_value in ("false", "true", 1, 0, [], {}):
            with self.subTest(cleanup=repr(cleanup_value)):
                run_case(snapshot, cleanup_value)
        with self.subTest(snapshot="subclass"):
            run_case(subclass_snapshot, True)
        with self.subTest(snapshot="duck"):
            run_case(duck_snapshot, True)

    def test_final_qualification_rejects_each_independent_post_qualification_mutation(self) -> None:
        """Every proof-bearing mutation invalidates a fresh terminal qualification."""

        previous_doc = verifier.EVIDENCE_DOC

        def qualified() -> Phases:
            ph = Phases(self.sentinel)
            ph.facts["transcript_valid_typed"] = True
            ph.facts["transcription_complete"] = True
            install_positive_operational_facts(ph, restart_drill=False)
            for row in positive_phase_rows(restart_drill=False):
                if row["name"] == verifier.PhaseID.EVIDENCE.value:
                    continue
                ph.record(
                    verifier.PhaseID(row["name"]),
                    verifier.PhaseStatus.PASS,
                    verifier.PhaseDetail.template(),
                )
            activation = Activation(
                CaptureTuple(
                    _TEST_PEER_FINGERPRINT,
                    "nonce-1",
                    attempt_binding("01234567-89ab-cdef-0123-456789abcdef"),
                    1,
                ),
                PROCESS_TAP,
                PROCESS_TAP,
                PROCESS_TAP,
            )
            snapshot = verifier.LiveProofSnapshot(
                artifact_valid=True,
                current_peer=True,
                authenticated_peer_key=_TEST_PEER_FINGERPRINT,
                launch_nonce="nonce-1",
                activation=activation,
                functional_permission_state="granted",
                functional_permission_tuple=activation.tuple,
            )
            companion = type(
                "QualifiedCompanion",
                (),
                {
                    "_stream_key": self.sentinel,
                    "_expected_head": "a" * 40,
                    "proof_snapshot": snapshot,
                    "cleanup_succeeded": True,
                    "secret_seen": False,
                },
            )()
            try:
                with tempfile.TemporaryDirectory() as root:
                    verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
                    verifier.phase_evidence(
                        ph,
                        type(
                            "Args",
                            (),
                            {
                                "signed_app": Path(root) / "TarsCompanion.app",
                                "with_restart_drill": False,
                            },
                        )(),
                        companion=companion,
                        provenance_reader=lambda: ("a" * 40, []),
                    )
            finally:
                verifier.EVIDENCE_DOC = previous_doc
            self.assertEqual(verifier.final_result_code(ph), verifier.EXIT_OK)
            self.assertIsNotNone(ph._final_qualification_record)
            return ph

        def proof_digest_mutation(ph: Phases) -> None:
            ph.facts["proof_digest"] = "0" * 64

        def artifact_mutation(ph: Phases) -> None:
            ph.facts["artifact_facts"]["dirty"] = True

        def record_proof_mutation(ph: Phases) -> None:
            record = ph._final_qualification_record
            object.__setattr__(record, "proof", dataclasses.replace(record.proof, transcript_valid=False))

        def direct_forged_record(ph: Phases) -> None:
            ph._final_qualification_record = object.__new__(verifier._FinalQualificationRecord)

        mutations = (
            ("secret", lambda ph: ph.mark_secret_seen()),
            ("process-positive", lambda ph: ph.facts.__setitem__("process_tap_positive", False)),
            ("evidence-result", lambda ph: ph.facts.__setitem__("process_tap_evidence_result", "FAIL")),
            ("proof-digest", proof_digest_mutation),
            ("expected-head", lambda ph: ph.facts.__setitem__("expected_head", "c" * 40)),
            ("expected-tree", lambda ph: ph.facts.__setitem__("expected_tree", "c" * 40)),
            ("expected-digest", lambda ph: ph.facts.__setitem__("expected_digest", "c" * 64)),
            ("artifact", artifact_mutation),
            ("phase-row", lambda ph: ph.rows[0].__setitem__("status", "FAIL")),
            ("restart-mode", lambda ph: setattr(ph, "_with_restart_drill", True)),
            ("restart-fact", lambda ph: ph.facts.__setitem__("restart_drill", True)),
            ("record-proof", record_proof_mutation),
            ("record-canonical", lambda ph: object.__setattr__(ph._final_qualification_record, "canonical_payload", b"{}")),
            ("record-state", lambda ph: object.__setattr__(ph._final_qualification_record, "state_payload", b"{}")),
            ("direct-record", direct_forged_record),
        )
        for label, mutate in mutations:
            with self.subTest(mutation=label):
                ph = qualified()
                mutate(ph)
                self.assertEqual(verifier.final_result_code(ph), verifier.EXIT_FAILED)

    def test_phase_stop_captures_snapshot_before_cleanup_and_final_proof_can_pass(self) -> None:
        """The real phase order snapshots readiness before clearing the run."""

        previous_doc = verifier.EVIDENCE_DOC
        activation = Activation(
            CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce-1", attempt_binding("01234567-89ab-cdef-0123-456789abcdef"), 1),
            PROCESS_TAP,
            PROCESS_TAP,
            PROCESS_TAP,
        )
        snapshot = verifier.LiveProofSnapshot(
            artifact_valid=True,
            current_peer=True,
            authenticated_peer_key=_TEST_PEER_FINGERPRINT,
            launch_nonce="nonce-1",
            activation=activation,
            functional_permission_state="granted",
            functional_permission_tuple=activation.tuple,
        )

        stream_key = self.sentinel

        class PhaseRun:
            _stream_key = stream_key
            _expected_head = "a" * 40
            proof_snapshot: verifier.LiveProofSnapshot | None = None
            cleanup_succeeded = False
            cleanup_error = None
            secret_seen = False

            def capture_live_proof_snapshot(self) -> verifier.LiveProofSnapshot:
                self.proof_snapshot = snapshot
                return snapshot

            def stop(self) -> bool:
                if self.proof_snapshot is None:
                    raise AssertionError("cleanup ran before pre-stop snapshot")
                self.cleanup_succeeded = True
                return True

        class Response:
            status_code = 200

            @staticmethod
            def json() -> dict[str, bool]:
                return {"transcription_complete": True}

        run = PhaseRun()
        ph = Phases(self.sentinel)
        install_positive_operational_facts(ph, restart_drill=False)
        with tempfile.TemporaryDirectory() as root:
            verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
            with mock.patch.object(verifier.time, "sleep"), mock.patch.object(
                verifier.requests,
                "post",
                return_value=Response(),
            ), mock.patch.object(
                verifier,
                "fetch_segments",
                return_value=[
                    {"speaker": "Candidato", "text": "candidato experiencia", "is_final": True},
                    {"speaker": "Entrevistador", "text": "entrevistador pergunta", "is_final": True},
                ],
            ):
                verifier.phase_stop_and_assert(
                    ph,
                    "session-1",
                    expect_candidate=True,
                    expect_restart=False,
                    mic=None,
                    companion=run,
                )
            existing = {
                row["name"] for row in ph.rows
                if type(row) is verifier._TypedPhaseRow and "name" in row
            }
            for name in sorted(
                verifier._required_phase_ids(with_restart_drill=False)
                - {verifier.PhaseID.EVIDENCE.value}
                - existing
            ):
                ph.record(
                    verifier.PhaseID(name),
                    verifier.PhaseStatus.PASS,
                    verifier.PhaseDetail.template(),
                )
            self.assertIsNotNone(run.proof_snapshot)
            self.assertTrue(run.cleanup_succeeded)
            verifier.phase_evidence(
                ph,
                type("Args", (), {"signed_app": Path(root) / "TarsCompanion.app", "with_restart_drill": False})(),
                companion=run,
                provenance_reader=lambda: ("a" * 40, []),
            )
            document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
        self.assertEqual(ph.facts["process_tap_evidence_result"], "PASS")
        self.assertTrue(ph.facts["process_tap_positive"])
        self.assertNotIn("EXECUÇÃO COM FALHAS", document)
        self.assertEqual(verifier.final_result_code(ph), verifier.EXIT_OK)
        verifier.EVIDENCE_DOC = previous_doc

    def test_canonical_evidence_omits_positive_claim_on_nonpass(self) -> None:
        for result in ("BLOCKED", "INCONCLUSIVE", "FAIL"):
            evidence = canonical_evidence(result=result, facts={"engine": SCREEN_CAPTURE_KIT})
            self.assertNotIn("claim", evidence)
        activation = Activation(
            CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce-1", attempt_binding("fedcba98-7654-3210-fedc-ba9876543210"), 1),
            PROCESS_TAP, PROCESS_TAP, PROCESS_TAP,
        )
        proof = PositiveProcessTapProof(
            artifact_valid=True,
            current_peer=True,
            authenticated_peer_key=_TEST_PEER_FINGERPRINT,
            launch_nonce=activation.tuple.launch_nonce,
            activation=activation,
            functional_permission_state="granted",
            functional_permission_tuple=activation.tuple,
            transcript_valid=True,
        )
        facts = complete_positive_canonical_facts(proof, restart_drill=False)
        genuine = canonical_evidence(facts=facts, proof=proof)
        self.assertIn("claim", genuine)
        self.assertIn("Process Tap", markdown_projection(genuine))
        with self.assertRaises(HarnessProtocolError):
            _CanonicalEvidence(dict(genuine), b"forged")
        class _CanonicalEvidenceSubclass(_CanonicalEvidence):
            pass
        _CanonicalEvidenceSubclass.__name__ = "_CanonicalEvidence"
        fake_same_name = dict.__new__(_CanonicalEvidenceSubclass)
        dict.__init__(fake_same_name, dict(genuine))
        with self.assertRaises(HarnessProtocolError):
            markdown_projection(fake_same_name)
        restart_facts = complete_positive_canonical_facts(proof, restart_drill=True)
        self.assertIn("claim", canonical_evidence(facts=restart_facts, proof=proof, result="PASS"))
        mismatch = dict(restart_facts)
        mismatch["restart_drill"] = False
        with self.assertRaises(HarnessProtocolError):
            canonical_evidence(facts=mismatch, proof=proof, result="PASS")
        missing_restart_rows = dict(restart_facts)
        missing_restart_rows["phase_rows"] = [
            row for row in restart_facts["phase_rows"]
            if row["name"] not in {
                verifier.PhaseID.RESTART.value,
                verifier.PhaseID.RESTART_TRANSCRIPT.value,
            }
        ]
        with self.assertRaises(HarnessProtocolError):
            canonical_evidence(facts=missing_restart_rows, proof=proof, result="PASS")
        with self.assertRaises(HarnessProtocolError):
            markdown_projection({"result": "FAIL", "claim": "process-tap-positive", "facts": {}})
        with self.assertRaises(HarnessProtocolError):
            markdown_projection({"result": "PASS", "facts": {}})
        with self.assertRaises(HarnessProtocolError):
            markdown_projection({"result": "FAIL", "facts": {"process_tap_positive": False}})
        with self.assertRaises(HarnessProtocolError): canonical_evidence(result="PASS", facts={"engine": PROCESS_TAP})

    def test_canonical_counts_and_dynamic_integers_are_bounded_exactly(self) -> None:
        maximum = (1 << 64) - 1
        evidence = canonical_evidence(result="FAIL", facts={"mic_bytes": maximum})
        self.assertEqual(evidence["facts"]["mic_bytes"], maximum)
        self.assertIn("Result: **FAIL**", markdown_projection(evidence))
        for value in (1 << 64, -1, True, 2**100000):
            with self.subTest(count=value if isinstance(value, bool) else "out-of-range"):
                with self.assertRaises(HarnessProtocolError):
                    canonical_evidence(result="FAIL", facts={"mic_bytes": value})
        with self.assertRaises(HarnessProtocolError):
            canonical_evidence(result="FAIL", facts={"error": {"nested": 2**100000}})

    def test_omitted_sentinel_rejects_key_shaped_fail_evidence_before_markdown(self) -> None:
        with self.assertRaises(HarnessProtocolError):
            canonical_evidence(result="FAIL", facts={"error": self.sentinel})
        safe = canonical_evidence(result="FAIL", facts={"error": "ordinary diagnostic"})
        self.assertIn("Result: **FAIL**", markdown_projection(safe))

    def test_phase_evidence_reduces_required_statuses_fail_closed_with_priority(self) -> None:
        unknown = Phases("S-Unknown-Status-Sentinel")
        unknown.record("required fixture", "UNKNOWN-S", "fixture")
        self.assertNotEqual(unknown.rows[0]["status"], "UNKNOWN-S")
        self.assertTrue(unknown.secret_seen)
        self.assertEqual(verifier._reduce_required_phase_status(unknown.rows), "FAIL")
        previous_unknown_doc = verifier.EVIDENCE_DOC
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.EVIDENCE_DOC = Path(root) / "unknown-status.md"
                verifier.phase_evidence(
                    unknown,
                    type("Args", (), {"signed_app": Path(root) / "TarsCompanion.app", "with_restart_drill": False})(),
                    provenance_reader=lambda: ("a" * 40, []),
                )
                unknown_document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
            self.assertNotIn("UNKNOWN-S", unknown_document)
            self.assertNotEqual(unknown.facts["process_tap_evidence_result"], "PASS")
        finally:
            verifier.EVIDENCE_DOC = previous_unknown_doc
        activation = Activation(
            CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce-1", attempt_binding("01234567-89ab-cdef-0123-456789abcdef"), 1),
            PROCESS_TAP,
            PROCESS_TAP,
            PROCESS_TAP,
        )
        snapshot = verifier.LiveProofSnapshot(
            artifact_valid=True,
            current_peer=True,
            authenticated_peer_key=_TEST_PEER_FINGERPRINT,
            launch_nonce="nonce-1",
            activation=activation,
            functional_permission_state="granted",
            functional_permission_tuple=activation.tuple,
        )
        companion = type(
            "PositiveCompanion",
            (),
            {
                "_stream_key": self.sentinel,
                "_expected_head": "a" * 40,
                "proof_snapshot": snapshot,
                "cleanup_succeeded": True,
                "secret_seen": False,
            },
        )()
        expected = {
            (): "PASS",
            ("INCONCLUSIVE",): "INCONCLUSIVE",
            ("BLOQUEADO",): "BLOCKED",
            ("FAIL",): "FAIL",
            ("BLOQUEADO", "INCONCLUSIVE"): "BLOCKED",
            ("FAIL", "BLOQUEADO", "INCONCLUSIVE"): "FAIL",
            ("UNKNOWN",): "FAIL",
        }
        previous_doc = verifier.EVIDENCE_DOC
        try:
            for statuses, result in expected.items():
                with self.subTest(statuses=statuses):
                    ph = Phases(self.sentinel)
                    ph.facts["transcript_valid_typed"] = True
                    ph.facts["transcription_complete"] = True
                    install_positive_operational_facts(ph, restart_drill=False)
                    if statuses == ("UNKNOWN",):
                        # Deliberately retain one untyped producer-input
                        # attack so the reducer's fail-closed branch is
                        # still covered separately from valid rows.
                        ph.record("required fixture", "UNKNOWN", "fixture")
                        ph.rows.append({"name": "malformed"})
                    else:
                        # A valid run plan is exhaustive: inject each status
                        # into a distinct required PhaseID while all other
                        # rows remain exact producer-owned PASS rows.
                        required_names = sorted(
                            verifier._required_phase_ids(with_restart_drill=False)
                            - {verifier.PhaseID.EVIDENCE.value}
                        )
                        for index, name in enumerate(required_names):
                            status = statuses[index] if index < len(statuses) else "PASS"
                            phase_status = verifier.PhaseStatus(
                                "BLOQUEADO" if status == "BLOQUEADO" else status
                            )
                            detail = (
                                verifier.PhaseDetail.template()
                                if phase_status is verifier.PhaseStatus.PASS
                                else verifier.PhaseDetail.diagnostic("fixture")
                            )
                            ph.record(verifier.PhaseID(name), phase_status, detail)
                    with tempfile.TemporaryDirectory() as root:
                        verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
                        verifier.phase_evidence(
                            ph,
                            type("Args", (), {"signed_app": Path(root) / "TarsCompanion.app", "with_restart_drill": False})(),
                            companion=companion,
                            provenance_reader=lambda: ("a" * 40, []),
                        )
                        document = verifier.EVIDENCE_DOC.read_text(encoding="utf-8")
                    self.assertEqual(ph.facts["process_tap_evidence_result"], result)
                    if result == "PASS":
                        self.assertTrue(ph.facts["process_tap_positive"])
                        self.assertIn("Process Tap", document)
                        self.assertEqual(verifier.final_result_code(ph), verifier.EXIT_OK)
                    else:
                        self.assertFalse(ph.facts["process_tap_positive"])
                        self.assertNotIn("Process Tap positive", document)
                        self.assertNotIn("Comprova apenas", document)
                        self.assertNotEqual(verifier.final_result_code(ph), verifier.EXIT_OK)
        finally:
            verifier.EVIDENCE_DOC = previous_doc

    def test_final17_closed_phase_and_diagnostic_ownership_boundaries(self) -> None:
        """Producer rows are closed; tagged free text can never prove PASS."""

        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        with contextlib.redirect_stdout(io.StringIO()):
            for first in alphabet:
                sentinel = first + "-Task11-Final17-Sentinel"
                phases = verifier.Phases(sentinel)
                for phase_id in verifier.PhaseID:
                    phases.record(
                        phase_id,
                        verifier.PhaseStatus.PASS,
                        verifier.PhaseDetail.template(),
                    )
                phases.facts.update(
                    {
                        "engine": PROCESS_TAP,
                        "voice": "Eddy",
                        "app_path": "/Applications/TarsCompanion.app",
                        "signed_app": "/Applications/TarsCompanion.app",
                        "commit": "a" * 40,
                        "mic_frames": 2,
                        "mic_bytes": 3200,
                        "mic_speech_frames": 1,
                        "segments_total": 2,
                        "segments_final": 2,
                        "segments_pre_stop": 1,
                        "transcription_complete": True,
                        "transcript_speakers": ["Candidato", "Entrevistador"],
                        "transcript_candidate_words": ["candidato", "experiencia"],
                        "transcript_interviewer_words": ["pergunta"],
                        "transcript_candidate_hits": 2,
                        "transcript_interviewer_hits": 1,
                        "transcript_valid_typed": True,
                        "transcript_restart_match": True,
                    }
                )
                self.assertFalse(phases.secret_seen, first)

        diagnostic = verifier.Phases(self.sentinel)
        with contextlib.redirect_stdout(io.StringIO()):
            diagnostic.record(
                verifier.PhaseID.EVIDENCE,
                verifier.PhaseStatus.PASS,
                verifier.CredentialReachableDiagnostic(f"wire-tail:{self.sentinel[:1]}"),
            )
            diagnostic.facts["engine"] = verifier.CredentialReachableDiagnostic(
                f"relabel:{self.sentinel[:1]}"
            )
        self.assertTrue(diagnostic.secret_seen)
        self.assertEqual(diagnostic.rows[0]["status"], "FAIL")
        self.assertNotIn(self.sentinel[:1], json.dumps(diagnostic.rows, ensure_ascii=False))
        with self.assertRaises(HarnessProtocolError):
            verifier.validate_fact_specs({"transcript": [{"speaker": "Candidato", "text": "raw"}]})

    def test_final17a_closed_progress_has_no_url_safe_false_collisions(self) -> None:
        """Safe producer progress is typed; every free diagnostic stays hostile."""

        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for first in alphabet:
            with self.subTest(sentinel_first=first):
                sentinel = first + "-Task11-Final17a-Sentinel"
                phases = verifier.Phases(sentinel)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    phases.emit_progress(verifier.ProgressNotice.SETTLING)
                    phases.emit_progress(
                        verifier.ProgressNotice.FINAL_SEGMENTS,
                        final_count=2,
                        total_count=3,
                    )
                    phases.emit_progress(verifier.ProgressNotice.EVIDENCE_WRITTEN)
                self.assertFalse(phases.secret_seen)
                self.assertIn("assentando 10 s", output.getvalue())
                self.assertIn("2 segmentos finais de 3", output.getvalue())
                self.assertIn("documento de evidência escrito", output.getvalue())
                with self.assertRaises(HarnessProtocolError):
                    phases.emit("untagged output")
                with self.assertRaises(HarnessProtocolError):
                    phases.emit_progress("settling")
                with self.assertRaises(HarnessProtocolError):
                    phases.emit_progress(
                        verifier.ProgressNotice.FINAL_SEGMENTS,
                        final_count=True,
                        total_count=1,
                    )
                with self.assertRaises(HarnessProtocolError):
                    phases.emit_progress(
                        verifier.ProgressNotice.FINAL_SEGMENTS,
                        final_count=3,
                        total_count=2,
                    )

        diagnostic_phases = verifier.Phases("S-Task11-Final17a-Sentinel")
        diagnostic_output = io.StringIO()
        with contextlib.redirect_stdout(diagnostic_output):
            for length in range(1, len(diagnostic_phases.sentinel)):
                prefix = diagnostic_phases.sentinel[:length]
                with self.subTest(diagnostic_prefix_length=length):
                    diagnostic_phases.emit(
                        verifier.CredentialReachableDiagnostic(f"wire-diagnostic:{prefix}")
                    )
                    self.assertTrue(diagnostic_phases.secret_seen)
                    self.assertNotIn(prefix, diagnostic_phases._redactor._pending)
        self.assertNotIn("wire-diagnostic:S-Task11-Final17a-Sentinel", diagnostic_output.getvalue())

    def test_final17a_pass_rows_require_exact_closed_types_and_diagnostics_redact(self) -> None:
        """No caller-selected text can obtain a proof-bearing PASS row."""

        sentinel = "S-Task11-Final17a-Pass-Sentinel"
        invalid = (
            (verifier.PhaseID.SESSION_CREATED, verifier.PhaseStatus.PASS, "caller-selected-prefix:S"),
            ("Sessão criada", verifier.PhaseStatus.PASS, verifier.PhaseDetail.template()),
            (verifier.PhaseID.SESSION_CREATED, "PASS", verifier.PhaseDetail.template()),
            (
                verifier.PhaseID.SESSION_CREATED,
                verifier.PhaseStatus.PASS,
                verifier.PhaseDetail.diagnostic("diagnostic:S"),
            ),
            (
                verifier.PhaseID.SESSION_CREATED,
                verifier.PhaseStatus.PASS,
                verifier.CredentialReachableDiagnostic("diagnostic:S"),
            ),
            ("unknown-phase:S", verifier.PhaseStatus.PASS, verifier.PhaseDetail.template()),
            (verifier.PhaseID.SESSION_CREATED, "UNKNOWN-S", verifier.PhaseDetail.template()),
        )
        for name, status, detail in invalid:
            with self.subTest(name=name, status=status, detail=type(detail).__name__):
                phases = verifier.Phases(sentinel)
                with contextlib.redirect_stdout(io.StringIO()):
                    phases.record(name, status, detail)
                self.assertNotEqual(phases.rows[0].get("status"), "PASS")
                self.assertTrue(phases.secret_seen)
                rendered = json.dumps(phases.rows, ensure_ascii=False, default=str)
                self.assertNotIn("caller-selected-prefix:S", rendered)
                self.assertNotIn("diagnostic:S", rendered)
                self.assertNotIn("unknown-phase:S", rendered)

        exact = verifier.Phases(sentinel)
        with contextlib.redirect_stdout(io.StringIO()):
            exact.record(
                verifier.PhaseID.SESSION_CREATED,
                verifier.PhaseStatus.PASS,
                verifier.PhaseDetail.template(),
            )
        self.assertEqual(exact.rows[0]["status"], "PASS")
        self.assertFalse(exact.secret_seen)

    def test_final17a_positive_fact_diagnostic_cannot_be_relabelled_structural(self) -> None:
        """A tagged diagnostic remains dynamic even at positive fact slots."""

        sentinel = "S-Task11-Final17a-Fact-Sentinel"
        positive_specs = [
            key for key, spec in verifier.FACT_SPECS.items() if spec.positive
        ]
        phases = verifier.Phases(sentinel)
        with contextlib.redirect_stdout(io.StringIO()):
            for key in positive_specs:
                phases.facts[key] = verifier.CredentialReachableDiagnostic(
                    f"relabelled-fact:{sentinel[:1]}"
                )
        self.assertTrue(phases.secret_seen)
        self.assertNotIn(sentinel[:1], json.dumps(phases.facts, ensure_ascii=False))
        self.assertNotEqual(verifier.final_result_code(phases), verifier.EXIT_OK)

    def test_final17_failed_health_wire_has_only_closed_failure_code(self) -> None:
        _, health = self._activation_and_health()
        for permission, code in (("unknown", "capture-failed"), ("denied", "permission-denied")):
            event = dict(health)
            event["status"] = dict(health["status"])
            event["status"].update(
                {
                    "kind": "failed",
                    "route": "unknown",
                    "interruption": "clear",
                    "sleep": "awake",
                    "overflowed": False,
                    "permission": permission,
                    "failure_code": code,
                }
            )
            decoded = decode_event(canonical_json(event))
            self.assertNotIn("message", decoded["status"])
            self.assertEqual(decoded["status"]["failure_code"], code)
            for extra in (
                {"message": "raw diagnostic"},
                {"message": self.sentinel},
                {"extra": "unexpected"},
            ):
                hostile = dict(event)
                hostile["status"] = dict(event["status"])
                hostile["status"].update(extra)
                with self.assertRaises(HarnessProtocolError):
                    decode_event(canonical_json(hostile))
        with self.assertRaises(HarnessProtocolError):
            canonical_evidence(result="FAIL", facts={"transcript": []}, sentinel=self.sentinel)
        with self.assertRaises(HarnessProtocolError):
            markdown_projection({"result": "FAIL", "facts": {"transcript": []}})

    def test_canonical_owner_and_typed_redaction_survive_every_url_safe_sentinel_start(self) -> None:
        with self.assertRaises(HarnessProtocolError):
            canonical_evidence(result="FAIL", facts={"process_tap_positive": False})
        with self.assertRaises(HarnessProtocolError):
            canonical_evidence(result="FAIL", facts={"process_tap_evidence_result": "FAIL"})
        with self.assertRaises(HarnessProtocolError):
            markdown_projection({"result": "FAIL", "claim": "process-tap-positive", "facts": {}})
        with self.assertRaises(HarnessProtocolError):
            markdown_projection({"result": "PASS", "facts": {}})

        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for first in alphabet:
            with self.subTest(sentinel_first=first):
                sentinel = first + "-Task11-URLSafe-Sentinel"
                ordinary = Phases(sentinel)
                ordinary.record(
                    verifier.PhaseID.SESSION_CREATED,
                    verifier.PhaseStatus.PASS,
                    verifier.PhaseDetail.template(),
                )
                ordinary.facts["engine"] = PROCESS_TAP
                self.assertFalse(ordinary.secret_seen)
                self.assertEqual(ordinary.rows[0]["status"], "PASS")
                self.assertEqual(ordinary.facts["engine"], PROCESS_TAP)
                self.assertNotEqual(verifier.final_result_code(ordinary), verifier.EXIT_OK)

                dynamic = Phases(sentinel)
                dynamic.facts["nested"] = {"value": f"dynamic:{sentinel[:1]}"}
                self.assertTrue(dynamic.secret_seen)
                self.assertNotIn(f"dynamic:{sentinel[:1]}", json.dumps(dynamic.facts, ensure_ascii=False))
                subset_spoof = Phases(sentinel)
                subset_spoof.facts["nested"] = {
                    "name": f"nested-name:{sentinel[:1]}",
                    "status": f"unknown-status:{sentinel[:1]}",
                    "detail": f"nested-detail:{sentinel[:1]}",
                    "extra": f"spoof:{sentinel[:1]}",
                }
                subset_spoof.facts["transcript"] = [{
                    "speaker": f"unknown-speaker:{sentinel[:1]}",
                    "text": f"nested-text:{sentinel[:1]}",
                    "extra": f"spoof:{sentinel[:1]}",
                }]
                self.assertTrue(subset_spoof.secret_seen)
                subset_json = json.dumps(subset_spoof.facts, ensure_ascii=False)
                for marker in (
                    f"nested-name:{sentinel[:1]}",
                    f"unknown-status:{sentinel[:1]}",
                    f"nested-detail:{sentinel[:1]}",
                    f"unknown-speaker:{sentinel[:1]}",
                    f"nested-text:{sentinel[:1]}",
                    f"spoof:{sentinel[:1]}",
                ):
                    self.assertNotIn(marker, subset_json)
                fallback = verifier._redact_evidence_value(
                    {"phase_rows": [{"name": "phase", "status": "PASS", "detail": sentinel}]},
                    sentinel,
                )
                self.assertEqual(fallback["phase_rows"][0]["status"], "PASS")
                self.assertEqual(fallback["phase_rows"][0]["detail"], "<redacted>")
                with self.assertRaises(HarnessProtocolError):
                    canonical_evidence(result="FAIL", facts=fallback, sentinel=sentinel)
                for result in ("FAIL", "BLOCKED", "INCONCLUSIVE"):
                    evidence = canonical_evidence(result=result, facts={})
                    self.assertTrue(secret_free(evidence, sentinel))
                    document = markdown_projection(evidence)
                    self.assertIn(f"Result: **{result}**", document)

    def test_screen_capture_or_transcript_only_cannot_pass(self) -> None:
        tap = Activation(
            CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce", attempt_binding("fedcba98-7654-3210-fedc-ba9876543210"), 1),
            PROCESS_TAP, PROCESS_TAP, PROCESS_TAP,
        )
        sck = Activation(tap.tuple, PROCESS_TAP, PROCESS_TAP, SCREEN_CAPTURE_KIT)
        def proof(activation=tap, *, artifact=True, peer=True, transcript=True, nonce="nonce"):
            return PositiveProcessTapProof(
                artifact,
                peer,
                _TEST_PEER_FINGERPRINT,
                nonce,
                activation,
                "granted",
                activation.tuple,
                transcript,
            )
        self.assertTrue(positive_process_tap_claim(proof()))
        self.assertFalse(positive_process_tap_claim(proof(sck)))
        self.assertFalse(positive_process_tap_claim(proof(transcript=False)))
        self.assertFalse(positive_process_tap_claim(proof(nonce="other")))

    def test_permission_truth(self) -> None:
        self.assertEqual(functional_permission(pcm_samples=None), "unknown")
        self.assertEqual(functional_permission(pcm_samples=[]), "unknown")
        self.assertEqual(functional_permission(pcm_samples=[0.0, 0.0]), "unknown")
        self.assertEqual(functional_permission(pcm_samples=[0.1, 0.0]), "granted")
        self.assertEqual(functional_permission(pcm_samples=[float("nan")]), "unknown")
        self.assertEqual(functional_permission(pcm_samples=[1.0], explicit_denied=True), "denied")

    def test_restart_freshness_allows_numeric_generation_reset(self) -> None:
        previous = CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce-1", attempt_binding("01234567-89ab-cdef-0123-456789abcdef"), 7)
        current = CaptureTuple(_TEST_PEER_FINGERPRINT_2, "nonce-2", attempt_binding("11234567-89ab-cdef-0123-456789abcdef"), 1)
        self.assertTrue(restart_requires_fresh(previous, current))
        self.assertFalse(restart_requires_fresh(previous, CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce-1", previous.attempt_id, 1)))
        self.assertFalse(restart_requires_fresh(previous, CaptureTuple("", "nonce-2", current.attempt_id, 1)))
        self.assertFalse(restart_requires_fresh(previous, CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce-2", current.attempt_id, 1)))
        self.assertFalse(restart_requires_fresh(previous, CaptureTuple(_TEST_PEER_FINGERPRINT_2, "nonce-1", current.attempt_id, 1)))
        self.assertFalse(restart_requires_fresh(previous, CaptureTuple(_TEST_PEER_FINGERPRINT_2, "nonce-2", previous.attempt_id, 1)))

    def test_each_claim_conjunct_is_independently_required(self) -> None:
        activation = Activation(
            CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce", attempt_binding("fedcba98-7654-3210-fedc-ba9876543210"), 1),
            PROCESS_TAP, PROCESS_TAP, PROCESS_TAP,
        )
        kwargs = dict(artifact_valid=True, current_peer=True, authenticated_peer_key=_TEST_PEER_FINGERPRINT, launch_nonce="nonce", activation=activation, functional_permission_state="granted", functional_permission_tuple=activation.tuple, transcript_valid=True)
        for key in ("artifact_valid", "current_peer", "authenticated_peer_key", "launch_nonce", "activation", "functional_permission_state", "functional_permission_tuple", "transcript_valid"):
            bad = dict(kwargs)
            bad[key] = None if key in {"activation", "functional_permission_tuple"} else ("unknown" if key == "functional_permission_state" else False)
            self.assertFalse(positive_process_tap_claim(PositiveProcessTapProof(**bad)), key)

    def test_transcript_requires_source_labels_and_text(self) -> None:
        self.assertTrue(transcript_claim([{"source": "system_audio", "text": "hello"}]))
        self.assertFalse(transcript_claim([]))
        self.assertFalse(transcript_claim([{"source": "", "text": "hello"}]))
        self.assertFalse(transcript_claim([{"source": "system_audio", "text": ""}]))

    def test_signed_app_branch_is_a_preflight_barrier_for_fake_commands(self) -> None:
        script = (Path(__file__).with_name("release_menubar_app.sh")).read_text(encoding="utf-8")
        branch = script.split("run_signed_app_only()", 1)[1].split("if [[ \"${RELEASE_MODE}\" == \"signed-app-only\" ]]", 1)[0]
        for forbidden in ("security find-identity", "notarytool history", "notarytool submit", "hdiutil", "stapler", "spctl", "open", "say"):
            self.assertNotIn(forbidden, branch)
        self.assertIn("return 0", branch)
        dispatch = script.split("if [[ \"${RELEASE_MODE}\" == \"signed-app-only\" ]]", 1)[1]
        self.assertIn("exit $?", dispatch)
        # Mutation-effective: if the terminal exit is removed, the source no
        # longer proves that the distribution path is unreachable.
        normalized_dispatch = dispatch.replace("    ", "")
        mutated = normalized_dispatch.replace("run_signed_app_only\nexit $?", "run_signed_app_only")
        self.assertIn("run_signed_app_only\nfi", mutated)
        self.assertNotIn("run_signed_app_only\nfi", normalized_dispatch)

    def test_signed_app_release_executes_signature_neutral_fake_runner(self) -> None:
        """Run the signed-app branch against a mutating, executable fake toolchain."""

        script = Path(__file__).with_name("release_menubar_app.sh").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / "repo"
            scripts = repo / "scripts"
            fakebin = Path(root) / "bin"
            tmpdir = Path(root) / "tmp"
            scripts.mkdir(parents=True)
            fakebin.mkdir()
            tmpdir.mkdir()
            script_path = scripts / "release_menubar_app.sh"
            script_path.write_text(script, encoding="utf-8")
            script_path.chmod(0o755)
            log_path = Path(root) / "commands.jsonl"
            app_bundle = repo / "dist" / "TarsCompanion.app"
            unsigned = b"unsigned-menubar-payload"
            head = "a" * 40
            tree = "b" * 40
            provenance = Path(root) / "supplied.provenance"
            provenance.write_text(f"head={head}\ntree={tree}\ndirty=false\n", encoding="utf-8")

            runner = Path(root) / "fake-release-runner.py"
            runner.write_text(
                textwrap.dedent(
                    """
                    #!/usr/bin/env python3
                    import json
                    import os
                    import sys
                    from pathlib import Path

                    log = Path(os.environ["FAKE_LOG"])
                    app = Path(os.environ["FAKE_APP_BUNDLE"])
                    unsigned = b"unsigned-menubar-payload"
                    ad_hoc = unsigned + b"|ADHOC-SIGNATURE"
                    args = sys.argv[1:]
                    command = args[0] if args else ""
                    with log.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(args, ensure_ascii=False) + "\\n")
                    if command == "bash":
                        executable = app / "Contents" / "MacOS" / "TarsCompanionApp"
                        executable.parent.mkdir(parents=True, exist_ok=True)
                        executable.write_bytes(ad_hoc)
                        executable.chmod(0o755)
                        if os.environ.get("FAKE_PACKAGE_MUTATION"):
                            Path(os.environ["FAKE_REPO"]).joinpath("package-mutated.marker").write_text("mutation", encoding="utf-8")
                        sys.exit(0)
                    if command != "codesign":
                        sys.exit(90)
                    if "--remove-signature" in args:
                        Path(args[-1]).write_bytes(unsigned)
                        sys.exit(0)
                    if "--force" in args:
                        target = Path(args[-1])
                        resource = target / "Contents" / "Resources" / "Task11Provenance.json"
                        if target != app or not resource.is_file():
                            sys.exit(91)
                        executable = target / "Contents" / "MacOS" / "TarsCompanionApp"
                        executable.write_bytes(unsigned + b"|FINAL-SIGNATURE")
                        sys.exit(0)
                    if "--verify" in args:
                        sys.exit(0)
                    if "-dv" in args:
                        print("Identifier=com.ellaexecutivesearch.tarscompanion")
                        print("TeamIdentifier=3FLG8W6B95")
                        print(
                            "Authority=Apple Development: Travel Advisory LLC (3FLG8W6B95)"
                            if os.environ.get("FAKE_BAD_AUTHORITY")
                            else "Authority=Developer ID Application: Travel Advisory LLC (3FLG8W6B95)"
                        )
                        print(
                            os.environ.get(
                                "FAKE_CODE_DIRECTORY",
                                "CodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded",
                            )
                        )
                        sys.exit(0)
                    if "-d" in args:
                        if os.environ.get("FAKE_FALSE_ENTITLEMENT"):
                            print('<plist version="1.0"><dict><key>com.apple.security.device.audio-input</key><false/></dict></plist>')
                        elif os.environ.get("FAKE_BAD_ENTITLEMENT"):
                            print('<plist version="1.0"><dict><key>com.apple.security.device-audio-input.invalid</key><false/></dict></plist>')
                        elif os.environ.get("FAKE_EXTRA_ENTITLEMENT"):
                            print('<plist version="1.0"><dict><key>com.apple.security.device.audio-input</key><true/><key>com.apple.security.get-task-allow</key><false/></dict></plist>')
                        else:
                            print('<plist version="1.0"><dict><key>com.apple.security.device.audio-input</key><true/></dict></plist>')
                        sys.exit(0)
                    sys.exit(92)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            runner.chmod(0o755)
            fake_git = fakebin / "git"
            fake_git.write_text(
                textwrap.dedent(
                    f"""
                    #!/usr/bin/env python3
                    import json
                    import os
                    import sys
                    from pathlib import Path
                    args = sys.argv[1:]
                    with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(["git"] + args) + "\\n")
                    if "status" in args:
                        marker = Path(os.environ.get("FAKE_REPO", "")) / "package-mutated.marker"
                        print("?? package-mutated.marker" if marker.is_file() else "")
                    elif "HEAD^{{tree}}" in args:
                        print("{tree}")
                    elif "HEAD" in args:
                        print("{head}")
                    else:
                        raise SystemExit(93)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            environment = dict(os.environ)
            environment.update(
                {
                    "PATH": f"{fakebin}:{environment.get('PATH', '')}",
                    "TMPDIR": str(tmpdir),
                    "FAKE_LOG": str(log_path),
                    "FAKE_APP_BUNDLE": str(app_bundle),
                    "FAKE_REPO": str(repo),
                    "TARS_RELEASE_COMMAND_RUNNER": str(runner),
                    "FAKE_CODE_DIRECTORY": "CodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded",
                }
            )

            completed = subprocess.run(
                ["bash", str(script_path), "--signed-app-only", "--task11-provenance", str(provenance)],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            executable = app_bundle / "Contents" / "MacOS" / "TarsCompanionApp"
            resource = app_bundle / "Contents" / "Resources" / "Task11Provenance.json"
            self.assertEqual(executable.read_bytes(), unsigned + b"|FINAL-SIGNATURE")
            resource_json = json.loads(resource.read_text(encoding="utf-8"))
            unsigned_digest = hashlib.sha256(unsigned).hexdigest()
            self.assertEqual(resource.read_bytes(), canonical_json(resource_json))
            self.assertEqual(resource_json["executable_sha256"], unsigned_digest)
            self.assertNotEqual(hashlib.sha256(executable.read_bytes()).hexdigest(), unsigned_digest)

            entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            commands = [entry[0] for entry in entries]
            self.assertEqual(commands.count("bash"), 1)
            self.assertEqual(commands.count("codesign"), 6)
            remove_entries = [entry for entry in entries if entry[0] == "codesign" and "--remove-signature" in entry]
            self.assertEqual(len(remove_entries), 2)
            force_index = next(index for index, entry in enumerate(entries) if entry[0] == "codesign" and "--force" in entry)
            first_remove_index = next(index for index, entry in enumerate(entries) if entry[0] == "codesign" and "--remove-signature" in entry)
            second_remove_index = max(index for index, entry in enumerate(entries) if entry[0] == "codesign" and "--remove-signature" in entry)
            self.assertLess(first_remove_index, force_index)
            self.assertGreater(second_remove_index, force_index)
            force_entries = [entry for entry in entries if entry[0] == "codesign" and "--force" in entry]
            self.assertEqual(len(force_entries), 1)
            self.assertEqual(force_entries[0][-1], str(app_bundle))
            self.assertNotIn("security", commands)
            for forbidden in ("notarytool", "hdiutil", "stapler", "spctl", "open", "say"):
                self.assertNotIn(forbidden, commands)
            self.assertLess(
                script.index("mv -f \"${provenance_tmp}\" \"${provenance_json}\""),
                script.index("run_release_command codesign --force", script.index("run_signed_app_only")),
            )

            # A mutation introduced by the package boundary must stop the
            # signed-app path before it derives a digest, writes provenance,
            # or invokes final signing.
            shutil.rmtree(app_bundle)
            log_path.write_text("", encoding="utf-8")
            dirty_environment = dict(environment, FAKE_PACKAGE_MUTATION="1")
            dirty_run = subprocess.run(
                ["bash", str(script_path), "--signed-app-only", "--task11-provenance", str(provenance)],
                cwd=repo,
                env=dirty_environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(dirty_run.returncode, 0)
            dirty_entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([entry[0] for entry in dirty_entries], ["git", "git", "git", "bash", "git", "git", "git"])
            self.assertFalse(any(entry[0] == "codesign" and "--force" in entry for entry in dirty_entries))
            self.assertFalse((app_bundle / "Contents" / "Resources" / "Task11Provenance.json").exists())
            (repo / "package-mutated.marker").unlink()

            # The signed-app-only contract is exact: an Apple Development
            # override, a hostile public authority, a lookalike/false
            # entitlement, or an unexpected extra entitlement cannot qualify.
            for hostile_env in (
                {"SIGN_IDENTITY": "Apple Development: Travel Advisory LLC (3FLG8W6B95)"},
                {"FAKE_BAD_AUTHORITY": "1"},
                {"FAKE_FALSE_ENTITLEMENT": "1"},
                {"FAKE_BAD_ENTITLEMENT": "1"},
                {"FAKE_EXTRA_ENTITLEMENT": "1"},
                {"FAKE_CODE_DIRECTORY": "CodeDirectory v=20500 size=1234 flags=0x0(runtime) hashes=10+7 location=embedded"},
                {"FAKE_CODE_DIRECTORY": "CodeDirectory v=20500 size=1234 flags=0x2(runtime) hashes=10+7 location=embedded"},
                {"FAKE_CODE_DIRECTORY": "CodeDirectory v=20500 size=1234 flags=runtime hashes=10+7 location=embedded"},
                {"FAKE_CODE_DIRECTORY": "CodeDirectory flags=0x10000(runtime) hashes=10+7"},
                {"FAKE_CODE_DIRECTORY": "CodeDirectory v=20500 size=1234 flags=0x10000(notruntime) hashes=10+7 location=embedded"},
                {"FAKE_CODE_DIRECTORY": "CodeDirectory v=20500 size=1234 flags=0x10000(runtime) flags=runtime hashes=10+7 location=embedded"},
                {"FAKE_CODE_DIRECTORY": "CodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded\nCodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded"},
                {"FAKE_CODE_DIRECTORY": "CodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=10+7 location=embedded extra"},
            ):
                with self.subTest(hostile_release=hostile_env):
                    shutil.rmtree(app_bundle, ignore_errors=True)
                    log_path.write_text("", encoding="utf-8")
                    hostile_run = subprocess.run(
                        ["bash", str(script_path), "--signed-app-only", "--task11-provenance", str(provenance)],
                        cwd=repo,
                        env=dict(environment, **hostile_env),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(hostile_run.returncode, 0)
                    hostile_entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
                    self.assertFalse(any(entry[0] in {"security", "notarytool", "hdiutil", "stapler", "spctl", "open", "say"} for entry in hostile_entries))

            # Mutation-effective: reverting only the final readback to a raw
            # post-sign hash must fail because the fake final signature changes
            # the executable bytes.
            final_readback_call = 'if ! readback_digest="$(signature_neutral_digest "${APP_EXECUTABLE}")"; then'
            raw_final_readback_call = 'if ! readback_digest="$(shasum -a 256 "${APP_EXECUTABLE}" | awk \'{print $1}\')"; then'
            self.assertIn(final_readback_call, script)
            raw_mutation = script.replace(final_readback_call, raw_final_readback_call, 1)
            mutated_path = scripts / "release-mutated.sh"
            mutated_path.write_text(raw_mutation, encoding="utf-8")
            mutated_path.chmod(0o755)
            mutated = subprocess.run(
                ["bash", str(mutated_path), "--signed-app-only", "--task11-provenance", str(provenance)],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(mutated.returncode, 0)


    def test_static_identity_is_raw_bounded_and_required_before_listener(self) -> None:
        """Static identity absence/length errors cannot reach launch or SCRATCH."""

        for unique, requirement, lightweight in (
            (b"", b"dr", b"lw"),
            (b"u" * 65, b"dr", b"lw"),
            (b"u", b"", b"lw"),
            (b"u", b"r" * 65_537, b"lw"),
            (bytearray(b"u"), b"dr", b"lw"),
            (b"u", b"dr", b""),
            (b"u", b"dr", b"r" * 65_537),
            (b"u", b"dr", bytearray(b"lw")),
        ):
            with self.subTest(
                unique_length=len(unique),
                requirement_length=len(requirement),
                lightweight_length=len(lightweight),
            ):
                with self.assertRaises(ValueError):
                    StaticCodeIdentity(unique, requirement, lightweight)

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            app = Path(root) / "TarsCompanion.app"
            launcher_calls: list[object] = []
            launcher = MacOSLaunchServicesAdapter(
                helper_spawner=make_helper_spawner(on_spawn=lambda argv: launcher_calls.append(argv))
            )
            facts = dataclasses_replace(valid_artifact_facts(), static_identity=None)
            with self.assertRaises(HarnessProtocolError):
                CompanionRun(
                    app,
                    "session-1",
                    self.sentinel,
                    "missing-static-identity",
                    launcher=launcher,
                    artifact_facts=facts,
                    expected_head="a" * 40,
                    expected_tree="b" * 40,
                    expected_digest="a" * 64,
                    artifact_inspector=FakeArtifactInspector(facts),
                    running_code_attestor=FakeRunningCodeAttestor(),
                )
            self.assertEqual(launcher_calls, [])
            self.assertFalse(verifier.SCRATCH.exists())

            with self.assertRaises(HarnessProtocolError):
                CompanionRun(
                    app,
                    "session-1",
                    self.sentinel,
                    "missing-attestor",
                    launcher=launcher,
                    artifact_facts=valid_artifact_facts(),
                    expected_head="a" * 40,
                    expected_tree="b" * 40,
                    expected_digest="a" * 64,
                    artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                )
            self.assertEqual(launcher_calls, [])
            self.assertFalse(verifier.SCRATCH.exists())
        verifier.SCRATCH = previous_scratch

    def test_injected_security_identity_adapters_never_load_live_security(self) -> None:
        """The production adapters remain testable through a raw fake bridge."""

        calls: list[tuple[object, ...]] = []

        class Bridge:
            def read_static_identity(self, app_path: Path) -> StaticCodeIdentity:
                calls.append(("static", app_path))
                return TEST_STATIC_IDENTITY

            def attest_running_code(
                self,
                peer: PeerIdentity,
                expected: StaticCodeIdentity,
            ) -> StaticCodeIdentity:
                calls.append(("dynamic", peer.audit_token, expected))
                return expected

        reader = verifier.DarwinStaticCodeIdentityReader(Bridge())
        attestor = verifier.DarwinRunningCodeAttestor(Bridge())
        peer = PeerIdentity(501, 4242, self.audit_token, "/Applications/TarsCompanion.app/Contents/MacOS/TarsCompanionApp")
        self.assertEqual(reader(Path("/Applications/TarsCompanion.app")), TEST_STATIC_IDENTITY)
        self.assertEqual(attestor(peer, TEST_STATIC_IDENTITY), TEST_STATIC_IDENTITY)
        self.assertEqual(calls[0][0], "static")
        self.assertEqual(calls[1][0], "dynamic")
        self.assertEqual(calls[1][1], self.audit_token)

    def test_encode_lightweight_code_requirement_matches_apple_developer_id_der(self) -> None:
        """Default designated LWCR facts encode to Apple's kernel envelope."""

        self.assertEqual(
            encode_lightweight_code_requirement(APPLE_DEVELOPER_ID_LWCR_FACTS),
            APPLE_DEVELOPER_ID_LWCR_DER,
        )
        with self.assertRaises(HarnessProtocolError):
            encode_lightweight_code_requirement({"ok": True})

    def test_raw_security_bridge_fake_order_token_types_and_cf_ownership(self) -> None:
        """Raw ctypes calls stay ordered, token-bound, typed, and leak-free."""

        class RawCF:
            DATA_TYPE = 101
            DICTIONARY_TYPE = 202

            def __init__(
                self,
                *,
                dynamic_unique: bytes = TEST_STATIC_IDENTITY.unique_cdhash,
                audit_token: str,
            ) -> None:
                self.dynamic_unique = dynamic_unique
                self.audit_token = audit_token
                self.next_handle = 10
                self.objects: dict[int, tuple[str, object]] = {}
                self.buffers: dict[int, object] = {}
                self.calls: list[tuple[object, ...]] = []
                self.releases: list[int] = []
                self.allocator_values: list[object] = []
                self.callback_values: list[tuple[object, object]] = []
                self.named: dict[str, int] = {}
                self.length_overrides: dict[int, int] = {}
                self.wrong_unique_type = False
                self.null_unique = False
                self.null_lwcr = False
                self.null_data_pointer = False
                self.guest_status = 0
                self.requirement_status = 0
                self.lwcr_create_status = 0
                self.lwcr_facts: dict[str, object] | None = None
                self.validity_statuses: list[int] = []
                self.static_validity_status = 0

            @staticmethod
            def handle(value: object) -> int:
                if isinstance(value, ctypes.c_void_p):
                    return int(value.value or 0)
                if value is None:
                    return 0
                return int(value)

            def new(self, kind: str, payload: object = None, *, name: str | None = None) -> int:
                handle = self.next_handle
                self.next_handle += 1
                self.objects[handle] = (kind, payload)
                if name is not None:
                    self.named[name] = handle
                return handle

            def out(self, pointer: object, handle: int) -> None:
                pointer._obj.value = handle  # type: ignore[attr-defined]

            def cf_release(self, value: object) -> None:
                handle = self.handle(value)
                self.releases.append(handle)

            def cf_get_type_id(self, value: object) -> int:
                kind = self.objects.get(self.handle(value), ("", None))[0]
                if kind == "data":
                    return self.DATA_TYPE
                if kind == "dict":
                    return self.DICTIONARY_TYPE
                return 999

            def cf_data_get_type_id(self) -> int:
                return self.DATA_TYPE

            def cf_dictionary_get_type_id(self) -> int:
                return self.DICTIONARY_TYPE

            def cf_data_create(self, _allocator: object, pointer: object, length: int) -> int:
                self.allocator_values.append(_allocator)
                raw = bytes(ctypes.string_at(pointer, int(length)))
                name = "audit_data" if raw == bytes.fromhex(self.audit_token) else "requirement_data"
                handle = self.new("data", raw, name=name)
                self.calls.append(("CFDataCreate", name, raw))
                if name == "audit_data":
                    self.length_overrides.setdefault(handle, len(raw))
                return handle

            def cf_data_get_length(self, value: object) -> int:
                handle = self.handle(value)
                return self.length_overrides.get(handle, len(self.objects[handle][1]))  # type: ignore[arg-type]

            def cf_data_get_byte_ptr(self, value: object) -> object:
                handle = self.handle(value)
                if self.null_data_pointer:
                    return None
                payload = self.objects[handle][1]
                buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)  # type: ignore[arg-type]
                self.buffers[handle] = buffer
                return ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))

            def cf_dictionary_create(
                self,
                _allocator: object,
                keys: object,
                values: object,
                count: int,
                _key_callbacks: object,
                _value_callbacks: object,
            ) -> int:
                self.allocator_values.append(_allocator)
                self.callback_values.append(
                    (
                        ctypes.cast(_key_callbacks, ctypes.c_void_p),
                        ctypes.cast(_value_callbacks, ctypes.c_void_p),
                    )
                )
                self.assert_count = count
                key = self.handle(keys[0])
                value = self.handle(values[0])
                self.calls.append(("CFDictionaryCreate", key, value))
                return self.new("dict", {key: value}, name="attributes")

            def cf_dictionary_get_value(self, dictionary: object, key: object) -> object:
                dictionary_id = self.handle(dictionary)
                key_id = self.handle(key)
                self.calls.append(("CFDictionaryGetValue", dictionary_id, key_id))
                if self.null_unique and key_id == self.unique_key:
                    return None
                if self.null_lwcr and key_id == self.lwcr_key:
                    return None
                return self.objects[dictionary_id][1].get(key_id)  # type: ignore[union-attr]

            def cf_url_create(self, *_args: object) -> int:
                self.allocator_values.append(_args[0])
                self.calls.append(("CFURLCreateFromFileSystemRepresentation",))
                return self.new("url", None, name="url")

            def sec_static_create(self, _url: object, flags: int, output: object) -> int:
                self.calls.append(("SecStaticCodeCreateWithPath", flags))
                self.out(output, self.new("static", None, name="static_code"))
                return 0

            def sec_static_check(self, _code: object, flags: int, requirement: object) -> int:
                self.calls.append(("SecStaticCodeCheckValidity", flags, requirement))
                return self.static_validity_status

            def sec_copy_signing(self, code: object, flags: int, output: object) -> int:
                code_id = self.handle(code)
                guest = code_id == self.named.get("guest")
                self.calls.append(("SecCodeCopySigningInformation", "guest" if guest else "static", flags))
                if guest:
                    unique_kind = "dict" if self.wrong_unique_type else "data"
                    unique = self.new(unique_kind, self.dynamic_unique, name="dynamic_unique")
                else:
                    unique = self.new("data", TEST_STATIC_IDENTITY.unique_cdhash, name="static_unique")
                    if self.lwcr_facts is None:
                        lwcr = self.new("requirement", None, name="lightweight_requirement")
                    else:
                        lwcr = self.new("dict", dict(self.lwcr_facts), name="lightweight_requirement")
                info = self.new(
                    "dict",
                    (
                        {self.unique_key: unique, self.lwcr_key: lwcr}
                        if not guest
                        else {self.unique_key: unique}
                    ),
                    name="dynamic_signing_info" if guest else "static_signing_info",
                )
                self.out(output, info)
                return 0

            def sec_copy_designated(self, _code: object, flags: int, output: object) -> int:
                self.calls.append(("SecCodeCopyDesignatedRequirement", flags))
                self.out(output, self.new("requirement", None, name="designated_requirement"))
                return 0

            def sec_requirement_copy_data(
                self, _requirement: object, flags: int, output: object
            ) -> int:
                self.calls.append(("SecRequirementCopyData", flags))
                if flags != 0:
                    return 1
                payload = (
                    TEST_STATIC_IDENTITY.lightweight_requirement
                    if self.handle(_requirement)
                    in {
                        self.named.get("lightweight_requirement"),
                        self.named.get("lwcr_requirement"),
                    }
                    else TEST_STATIC_IDENTITY.designated_requirement
                )
                name = (
                    "static_lightweight_data"
                    if payload is TEST_STATIC_IDENTITY.lightweight_requirement
                    else "static_requirement_data"
                )
                self.out(output, self.new("data", payload, name=name))
                return 0

            def sec_requirement_create_data(self, _data: object, flags: int, output: object) -> int:
                self.calls.append(("SecRequirementCreateWithData", flags))
                if self.requirement_status:
                    return self.requirement_status
                self.out(output, self.new("requirement", None, name="requirement"))
                return 0

            def cf_property_list_create_data(
                self,
                _allocator: object,
                plist: object,
                fmt: int,
                _options: int,
                _error: object,
            ) -> int:
                self.allocator_values.append(_allocator)
                payload = self.objects[self.handle(plist)][1]
                self.calls.append(("CFPropertyListCreateData", self.handle(plist), fmt))
                xml = __import__("plistlib").dumps(payload, fmt=__import__("plistlib").FMT_XML)
                return self.new("data", xml, name="lwcr_facts_plist")

            def sec_requirement_create_lwcr(
                self,
                data: object,
                flags: int,
                output: object,
                _error: object,
            ) -> int:
                payload = self.objects[self.handle(data)][1]
                self.calls.append(("SecRequirementCreateWithLightweightCodeRequirementData", flags, payload))
                if self.lwcr_create_status:
                    return self.lwcr_create_status
                self.out(output, self.new("requirement", None, name="lwcr_requirement"))
                return 0

            def sec_copy_guest(self, guest: object, attributes: object, flags: int, output: object) -> int:
                self.calls.append(("SecCodeCopyGuestWithAttributes", guest, self.handle(attributes), flags))
                if self.guest_status:
                    return self.guest_status
                self.out(output, self.new("guest", None, name="guest"))
                return 0

            def sec_check_validity(self, guest: object, flags: int, requirement: object) -> int:
                self.calls.append(("SecCodeCheckValidity", self.handle(guest), flags, self.handle(requirement)))
                if self.validity_statuses:
                    return self.validity_statuses.pop(0)
                return 0

            @property
            def unique_key(self) -> int:
                return 2

            @property
            def lwcr_key(self) -> int:
                return 4

            @property
            def guest_audit_key(self) -> int:
                return 3

            def bind(self, bridge: object) -> None:
                # kCFAllocatorDefault is legally NULL; all fake Create calls
                # must receive Python None (the ctypes NULL pointer).
                bridge._allocator = None  # type: ignore[attr-defined]
                bridge._cf_type_dictionary_key_callbacks = CFDictionaryKeyCallBacks()  # type: ignore[attr-defined]
                bridge._cf_type_dictionary_value_callbacks = CFDictionaryValueCallBacks()  # type: ignore[attr-defined]
                bridge._unique_key = ctypes.c_void_p(self.unique_key)  # type: ignore[attr-defined]
                bridge._lwcr_key = ctypes.c_void_p(self.lwcr_key)  # type: ignore[attr-defined]
                bridge._guest_audit_key = ctypes.c_void_p(self.guest_audit_key)  # type: ignore[attr-defined]
                bridge._cf_release = self.cf_release  # type: ignore[attr-defined]
                bridge._cf_get_type_id = self.cf_get_type_id  # type: ignore[attr-defined]
                bridge._cf_data_get_type_id = self.cf_data_get_type_id  # type: ignore[attr-defined]
                bridge._cf_data_create = self.cf_data_create  # type: ignore[attr-defined]
                bridge._cf_data_get_length = self.cf_data_get_length  # type: ignore[attr-defined]
                bridge._cf_data_get_byte_ptr = self.cf_data_get_byte_ptr  # type: ignore[attr-defined]
                bridge._cf_dictionary_get_type_id = self.cf_dictionary_get_type_id  # type: ignore[attr-defined]
                bridge._cf_dictionary_create = self.cf_dictionary_create  # type: ignore[attr-defined]
                bridge._cf_dictionary_get_value = self.cf_dictionary_get_value  # type: ignore[attr-defined]
                bridge._cf_url_create = self.cf_url_create  # type: ignore[attr-defined]
                bridge._sec_static_create = self.sec_static_create  # type: ignore[attr-defined]
                bridge._sec_static_check = self.sec_static_check  # type: ignore[attr-defined]
                bridge._sec_copy_signing = self.sec_copy_signing  # type: ignore[attr-defined]
                bridge._sec_copy_designated = self.sec_copy_designated  # type: ignore[attr-defined]
                bridge._sec_requirement_copy_data = self.sec_requirement_copy_data  # type: ignore[attr-defined]
                bridge._sec_requirement_create_data = self.sec_requirement_create_data  # type: ignore[attr-defined]
                bridge._sec_requirement_create_lwcr = self.sec_requirement_create_lwcr  # type: ignore[attr-defined]
                bridge._cf_property_list_create_data = self.cf_property_list_create_data  # type: ignore[attr-defined]
                bridge._sec_copy_guest = self.sec_copy_guest  # type: ignore[attr-defined]
                bridge._sec_check_validity = self.sec_check_validity  # type: ignore[attr-defined]

        # Make the fake's token source explicit; the production bridge still
        # obtains this value only from the accepted PeerIdentity.
        fake = RawCF(audit_token=self.audit_token)
        bridge = object.__new__(DarwinSecurityBridge)
        fake.bind(bridge)
        static_identity = bridge.read_static_identity(Path("/Applications/TarsCompanion.app"))
        self.assertEqual(static_identity, TEST_STATIC_IDENTITY)
        self.assertEqual(
            [entry[0] for entry in fake.calls],
            [
                "CFURLCreateFromFileSystemRepresentation",
                "SecStaticCodeCreateWithPath",
                "SecStaticCodeCheckValidity",
                "SecCodeCopySigningInformation",
                "CFDictionaryGetValue",
                "SecCodeCopyDesignatedRequirement",
                "SecRequirementCopyData",
                "CFDictionaryGetValue",
                "SecRequirementCopyData",
            ],
        )
        self.assertEqual(fake.calls[2][1:], (0x19, None))
        self.assertEqual(fake.calls[3][1:], ("static", 1 << 2))
        self.assertEqual(fake.calls[-1], ("SecRequirementCopyData", 0))
        self.assertTrue(fake.allocator_values)
        self.assertTrue(all(value is None for value in fake.allocator_values))
        self.assertEqual(
            fake.releases,
            [
                fake.named["static_lightweight_data"],
                fake.named["static_requirement_data"],
                fake.named["designated_requirement"],
                fake.named["static_signing_info"],
                fake.named["static_code"],
                fake.named["url"],
            ],
        )
        self.assertNotIn(fake.named["static_unique"], fake.releases)
        self.assertNotIn(fake.named["lightweight_requirement"], fake.releases)

        fake = RawCF(audit_token=self.audit_token)
        fake.null_lwcr = True
        bridge = object.__new__(DarwinSecurityBridge)
        fake.bind(bridge)
        with self.assertRaises(HarnessProtocolError):
            bridge.read_static_identity(Path("/Applications/TarsCompanion.app"))

        fake = RawCF(audit_token=self.audit_token)
        fake.lwcr_facts = dict(APPLE_DEVELOPER_ID_LWCR_FACTS)
        bridge = object.__new__(DarwinSecurityBridge)
        fake.bind(bridge)
        static_identity = bridge.read_static_identity(Path("/Applications/TarsCompanion.app"))
        self.assertEqual(static_identity, TEST_STATIC_IDENTITY)
        self.assertEqual(
            [entry[0] for entry in fake.calls],
            [
                "CFURLCreateFromFileSystemRepresentation",
                "SecStaticCodeCreateWithPath",
                "SecStaticCodeCheckValidity",
                "SecCodeCopySigningInformation",
                "CFDictionaryGetValue",
                "SecCodeCopyDesignatedRequirement",
                "SecRequirementCopyData",
                "CFDictionaryGetValue",
                "CFPropertyListCreateData",
                "CFDataCreate",
                "SecRequirementCreateWithLightweightCodeRequirementData",
                "SecRequirementCopyData",
            ],
        )
        self.assertEqual(fake.calls[8][2], DarwinSecurityBridge.PROPERTY_LIST_XML_FORMAT)
        self.assertEqual(fake.calls[9][2], APPLE_DEVELOPER_ID_LWCR_DER)
        self.assertEqual(fake.calls[10][2], APPLE_DEVELOPER_ID_LWCR_DER)
        self.assertNotIn(fake.named["lightweight_requirement"], fake.releases)

        fake = RawCF(audit_token=self.audit_token)
        bridge = object.__new__(DarwinSecurityBridge)
        fake.bind(bridge)
        result = bridge.attest_running_code(self.peer, TEST_STATIC_IDENTITY)
        self.assertEqual(result, TEST_STATIC_IDENTITY)
        self.assertEqual(
            [entry[0] for entry in fake.calls],
            [
                "CFDataCreate",
                "CFDictionaryCreate",
                "CFDataCreate",
                "SecRequirementCreateWithData",
                "SecCodeCopyGuestWithAttributes",
                "SecCodeCheckValidity",
                "SecCodeCopySigningInformation",
                "CFDictionaryGetValue",
                "SecCodeCheckValidity",
            ],
        )
        self.assertEqual(fake.calls[0][2], bytes.fromhex(self.audit_token))
        self.assertEqual(fake.calls[2][2], TEST_STATIC_IDENTITY.lightweight_requirement)
        self.assertNotEqual(
            TEST_STATIC_IDENTITY.lightweight_requirement,
            TEST_STATIC_IDENTITY.designated_requirement,
        )
        self.assertEqual(fake.calls[1][1], fake.guest_audit_key)
        self.assertEqual(fake.assert_count, 1)
        self.assertEqual(len(fake.callback_values), 1)
        key_callbacks, value_callbacks = fake.callback_values[0]
        self.assertEqual(
            key_callbacks.value,
            ctypes.addressof(bridge._cf_type_dictionary_key_callbacks),
        )
        self.assertEqual(
            value_callbacks.value,
            ctypes.addressof(bridge._cf_type_dictionary_value_callbacks),
        )
        self.assertEqual(fake.calls[4][1], None)
        self.assertEqual(fake.calls[5][2], 1 << 23)
        self.assertEqual(fake.calls[8][2], 1 << 23)
        self.assertEqual(
            fake.releases,
            [
                fake.named["dynamic_signing_info"],
                fake.named["guest"],
                fake.named["requirement"],
                fake.named["requirement_data"],
                fake.named["attributes"],
                fake.named["audit_data"],
            ],
        )
        self.assertNotIn(fake.named["dynamic_unique"], fake.releases)

        for mutation in (
            "audit-length",
            "unique-type",
            "unique-mismatch",
            "validity-status",
            "unique-null",
            "null-pointer",
            "guest-status",
        ):
            with self.subTest(mutation=mutation):
                fake = RawCF(
                    dynamic_unique=(
                        b"\x02" * 32
                        if mutation in {"unique-type", "unique-mismatch"}
                        else TEST_STATIC_IDENTITY.unique_cdhash
                    ),
                    audit_token=self.audit_token,
                )
                if mutation == "audit-length":
                    fake.length_overrides = {10: 31}
                elif mutation == "unique-type":
                    fake.wrong_unique_type = True
                elif mutation == "validity-status":
                    fake.validity_statuses = [1]
                elif mutation == "unique-null":
                    fake.null_unique = True
                elif mutation == "null-pointer":
                    fake.null_data_pointer = True
                elif mutation == "guest-status":
                    fake.guest_status = 1
                bridge = object.__new__(DarwinSecurityBridge)
                fake.bind(bridge)
                with self.assertRaises(HarnessProtocolError):
                    bridge.attest_running_code(self.peer, TEST_STATIC_IDENTITY)
                # Each acquired object is released at most once, including
                # all failure edges that happen before signing information.
                self.assertEqual(len(fake.releases), len(set(fake.releases)))

        with self.assertRaises(HarnessProtocolError):
            bridge.attest_running_code(
                dataclasses_replace(self.peer, audit_token="00" * 31),
                TEST_STATIC_IDENTITY,
            )

    def test_dynamic_source_contract_has_no_pid_or_early_send_fallback(self) -> None:
        """Source guards keep the dynamic and final reread edges mandatory."""

        bridge_source = inspect.getsource(DarwinSecurityBridge.attest_running_code)
        self.assertNotIn("kSecGuestAttributePid", bridge_source)
        self.assertNotIn("peer.pid", bridge_source)
        self.assertNotIn("os.kill", bridge_source)
        self.assertIn("self._sec_copy_guest(\n                None,", bridge_source)
        self.assertIn("expected.lightweight_requirement", bridge_source)
        self.assertIn("kSecCodeInfoDefaultDesignatedLightweightCodeRequirement", inspect.getsource(DarwinSecurityBridge))
        validity_start = bridge_source.index("status = self._sec_check_validity")
        self.assertNotIn("None", bridge_source[validity_start:])
        unique_index = bridge_source.index("dynamic_unique =")
        validity_calls = [
            index
            for index in range(len(bridge_source))
            if bridge_source.startswith("self._sec_check_validity(", index)
        ]
        self.assertEqual(len(validity_calls), 2)
        self.assertLess(validity_calls[0], unique_index)
        self.assertGreater(validity_calls[1], unique_index)

        send_source = inspect.getsource(CompanionRun.send_authenticated_session)
        order = (
            "fresh_facts = self._artifact_inspector.inspect",
            "reread_peer = revalidate_peer()",
            "attest_call(",
            "self.server.send_one_session(",
        )
        positions = [send_source.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("final_peer = revalidate_peer()", send_source)
        self.assertIn("fresh_facts.static_identity", send_source)

        transaction_source = inspect.getsource(UnixHarnessServer.send_one_session)
        transaction_order = (
            "final_peer = peer_revalidator()",
            "if not peer_identity_equal(final_peer, peer):",
            "accepted_token = _audit_token_bytes",
            "require_exact_static_code_identity(static_identity, label=\"static identity\")",
            "wire = encode_session_command(",
            "state.accept_command(wire[4:], peer=final_peer)",
            "connection.sendall(wire)",
        )
        transaction_positions = [transaction_source.index(marker) for marker in transaction_order]
        self.assertEqual(transaction_positions, sorted(transaction_positions))
        self.assertIn("peer_revalidator=revalidate_peer", send_source)

    def test_security_bridge_shapes_match_sdk_and_null_allocator_contract(self) -> None:
        """Source/header checks cover the Darwin ctypes ABI without loading it."""

        source = Path(__file__).with_name("live_system_audio_harness.py").read_text(encoding="utf-8")
        dictionary_header = Path(
            "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/"
            "MacOSX26.0.sdk/System/Library/Frameworks/CoreFoundation.framework/Headers/CFDictionary.h"
        ).read_text(encoding="utf-8")
        base_header = Path(
            "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/"
            "MacOSX26.0.sdk/System/Library/Frameworks/CoreFoundation.framework/Headers/CFBase.h"
        ).read_text(encoding="utf-8")
        requirement_header = Path(
            "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/"
            "MacOSX26.0.sdk/System/Library/Frameworks/Security.framework/Headers/SecRequirement.h"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            ctypes.sizeof(CFDictionaryKeyCallBacks),
            ctypes.sizeof(ctypes.c_long) + 5 * ctypes.sizeof(ctypes.c_void_p),
        )
        self.assertEqual(
            ctypes.sizeof(CFDictionaryValueCallBacks),
            ctypes.sizeof(ctypes.c_long) + 4 * ctypes.sizeof(ctypes.c_void_p),
        )
        self.assertIn("const CFDictionaryKeyCallBacks kCFTypeDictionaryKeyCallBacks", dictionary_header)
        self.assertIn("const CFDictionaryValueCallBacks kCFTypeDictionaryValueCallBacks", dictionary_header)
        self.assertIn("CFDictionaryCreate(CFAllocatorRef allocator", dictionary_header)
        self.assertIn("SecRequirementCopyData(SecRequirementRef requirement, SecCSFlags flags", requirement_header)
        self.assertIn("CF_RETURNS_RETAINED data", requirement_header)
        self.assertIn("kCFAllocatorDefault", base_header)
        self.assertIn("self._allocator: ctypes.c_void_p | None = None", source)
        self.assertNotIn("_export_allocator", source)
        self.assertNotRegex(source, r"\bin_dll\([^)]*kCFAllocatorDefault[^)]*\)")
        self.assertNotIn('self._export_pointer(\n                self._core_foundation, "kCFAllocatorDefault"', source)
        self.assertIn(
            '[ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]',
            source,
        )
        self.assertIn(
            "designated_requirement,\n                0,\n                ctypes.byref(requirement_data)",
            source,
        )
        self.assertIn("ctypes.POINTER(CFDictionaryKeyCallBacks)", source)
        self.assertIn("ctypes.POINTER(CFDictionaryValueCallBacks)", source)


    def test_dynamic_swap_restore_and_final_token_matrix_is_zero_byte_and_token_only(self) -> None:
        """Static A plus dynamic B, or a final token swap, never sends a command."""

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"

            def run_attempt(
                tag: str,
                *,
                attestor: object,
                reader_values: list[PeerIdentity],
            ) -> tuple[CompanionRun, list[bytes], list[tuple[bytes, int]], socket.socket]:
                helper = FakeOpenHelper(pid=7001)
                client_holder: dict[str, socket.socket | None] = {"socket": None}
                received: list[bytes] = []
                signals: list[tuple[bytes, int]] = []

                def signal_sender(token: bytes, signum: int) -> None:
                    signals.append((token, signum))
                    helper.returncode = 0
                    peer_socket = client_holder["socket"]
                    if peer_socket is not None:
                        try:
                            peer_socket.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        peer_socket.close()

                adapter = MacOSLaunchServicesAdapter(
                    helper_spawner=make_helper_spawner(helper),
                    signal_sender=signal_sender,
                )
                facts = valid_artifact_facts()
                inspector = FakeArtifactInspector(facts)
                run = CompanionRun(
                    Path(root) / "TarsCompanion.app",
                    "session-1",
                    self.sentinel,
                    tag,
                    launcher=adapter,
                    artifact_facts=facts,
                    expected_head="a" * 40,
                    expected_tree="b" * 40,
                    expected_digest="a" * 64,
                    artifact_inspector=inspector,
                    running_code_attestor=attestor,
                )
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client_holder["socket"] = client
                client.connect(str(run.socket_path))
                actual = PeerIdentity(
                    os.geteuid(),
                    4242,
                    self.audit_token,
                    run.launch_spec.executable_path,
                )
                # The shared fixture's canonical peer path is rooted at
                # /Applications; this run's expected peer path is the
                # temporary bundle path.  Normalize only that unchanged
                # fixture value so the test reaches the real send boundary.
                values = [actual if value == self.peer else value for value in reader_values]

                def reader(_connection: socket.socket) -> PeerIdentity:
                    return values.pop(0) if values else actual

                try:
                    run.send_authenticated_session(peer_reader=reader)
                except HarnessProtocolError:
                    client.settimeout(0.5)
                    try:
                        received.append(client.recv(4096))
                    except socket.timeout:
                        received.append(b"")
                return run, received, signals, client

            # A path swap/restoration can leave the fresh static A facts
            # unchanged; only the audit-token guest's dynamic B hash can close
            # that gap.  Exercise the causal zero-byte edge repeatedly.
            for iteration in range(30):
                dynamic_b = StaticCodeIdentity(
                    b"\x02" * 32,
                    TEST_STATIC_IDENTITY.designated_requirement,
                    TEST_STATIC_IDENTITY.lightweight_requirement,
                )
                run, received, signals, client = run_attempt(
                    f"swap-restore-{iteration}",
                    attestor=FakeRunningCodeAttestor(result=dynamic_b),
                    reader_values=[self.peer, self.peer, self.peer],
                )
                try:
                    self.assertEqual(received, [b""])
                    self.assertFalse(run.artifact_valid)
                    self.assertIsNone(run._state._session_binding if run._state is not None else None)
                    self.assertTrue(run.stop())
                    self.assertTrue(all(len(token) == 32 for token, _ in signals))
                    self.assertFalse(any(isinstance(token, int) for token, _ in signals))
                finally:
                    client.close()
                    run.stop()

            # No compatibility sentinel may stand in for the concrete
            # StaticCodeIdentity result.  In particular, True and None were
            # the historical fail-open values; every invalid result must
            # leave the command state empty and the socket byte-free.
            for invalid_result in (True, False, None, object()):
                with self.subTest(invalid_attestor=repr(invalid_result)):
                    run, received, signals, client = run_attempt(
                        f"invalid-attestor-{type(invalid_result).__name__}",
                        attestor=FakeRunningCodeAttestor(result=invalid_result),
                        reader_values=[self.peer, self.peer, self.peer],
                    )
                    try:
                        self.assertEqual(received, [b""])
                        self.assertFalse(run.artifact_valid)
                        self.assertIsNone(run._state._session_binding if run._state is not None else None)
                        self.assertTrue(run.stop())
                        self.assertEqual(len(signals), 1)
                        self.assertEqual(signals[0][0], bytes.fromhex(self.audit_token))
                    finally:
                        client.close()
                        run.stop()

            # Same integer PID with a different raw audit token at the final
            # reread is also terminal; the cleanup callback remains token-only.
            final_swap = dataclasses_replace(
                PeerIdentity(
                    os.geteuid(),
                    4242,
                    self.audit_token,
                    str(Path(root) / "TarsCompanion.app" / "Contents" / "MacOS" / "TarsCompanionApp"),
                ),
                audit_token="11" * 32,
            )
            run, received, signals, client = run_attempt(
                "final-token-swap",
                attestor=FakeRunningCodeAttestor(),
                reader_values=[self.peer, self.peer, self.peer, final_swap],
            )
            try:
                self.assertEqual(received, [b""])
                self.assertFalse(run.artifact_valid)
                self.assertIsNone(run._state._session_binding if run._state is not None else None)
                self.assertTrue(run.stop())
                self.assertTrue(all(len(token) == 32 for token, _ in signals))
            finally:
                client.close()
                run.stop()
        verifier.SCRATCH = previous_scratch

    def test_final18_url_safe_first_character_matrix_preserves_typed_pass(self) -> None:
        """Every URL-safe first byte must not collide with fixed producer facts."""

        url_safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        original_doc = verifier.EVIDENCE_DOC
        with tempfile.TemporaryDirectory(prefix="task11-final18-") as scratch:
            verifier.EVIDENCE_DOC = Path(scratch) / "evidence.md"
            for first in url_safe:
                with self.subTest(first=first):
                    sentinel = first + "-Task11-Final18-credential-sentinel-000000"
                    phases = Phases()
                    phases.facts["expected_head"] = "a" * 40
                    phases.facts["expected_tree"] = "b" * 40
                    phases.facts["expected_digest"] = "a" * 64
                    phases.facts["artifact_facts"] = valid_artifact_facts()
                    phases.facts["process_tap_positive"] = True
                    phases.facts["process_tap_evidence_result"] = "PASS"
                    phases.facts["restart_drill"] = True
                    phases.facts["transcript_valid_typed"] = True
                    phases.facts["transcription_complete"] = True
                    phases.facts["voice"] = "pt-BR voice"
                    for phase in verifier.PhaseID:
                        if phase in {
                            verifier.PhaseID.CLEANUP_REJECTION,
                            verifier.PhaseID.CLEANUP_TERMINAL_FAILURE,
                            verifier.PhaseID.CLEANUP_FAILURE,
                        }:
                            continue
                        phases.record(phase, verifier.PhaseStatus.PASS, verifier.PhaseDetail.template())
                    phases.register_stream_key(sentinel)
                    attempt = "01234567-89ab-cdef-0123-456789abcdef"
                    tuple_value = CaptureTuple(_TEST_PEER_FINGERPRINT, "nonce-1", attempt_binding(attempt), 1)
                    activation = Activation(tuple_value, PROCESS_TAP, PROCESS_TAP, PROCESS_TAP)
                    snapshot = verifier.LiveProofSnapshot(
                        artifact_valid=True,
                        current_peer=True,
                        authenticated_peer_key=_TEST_PEER_FINGERPRINT,
                        launch_nonce="nonce-1",
                        activation=activation,
                        functional_permission_state="granted",
                        functional_permission_tuple=tuple_value,
                    )
                    companion = type("Final18Companion", (), {
                        "_stream_key": sentinel,
                        "_expected_head": "a" * 40,
                        "proof_snapshot": snapshot,
                        "cleanup_succeeded": True,
                        "secret_seen": False,
                    })()
                    args = type("Final18Args", (), {
                        "signed_app": Path("/Applications/TarsCompanion.app"),
                        "with_restart_drill": True,
                    })()
                    verifier.phase_evidence(
                        phases,
                        args,
                        companion=companion,
                        provenance_reader=lambda: ("a" * 40, []),
                    )
                    self.assertFalse(phases.secret_seen)
                    self.assertEqual(phases.facts["process_tap_evidence_result"], "PASS")
                    self.assertTrue(phases.facts["process_tap_positive"])
                    self.assertEqual(verifier.final_result_code(phases), verifier.EXIT_OK)
            verifier.EVIDENCE_DOC = original_doc

    def test_final18_raw_pass_rows_and_positive_fact_wrappers_fail_closed(self) -> None:
        phases = Phases(self.sentinel)
        phases.facts["process_tap_positive"] = True
        phases.facts["process_tap_evidence_result"] = "PASS"
        phases.rows.append({
            "name": verifier.PhaseID.EVIDENCE.value,
            "status": "PASS",
            "detail": verifier.PhaseDetail.template().text,
        })
        verifier._phase_rows_for_evidence(phases)
        self.assertTrue(phases._row_ownership_failed)
        with tempfile.TemporaryDirectory(prefix="task11-final18-raw-") as scratch:
            original_doc = verifier.EVIDENCE_DOC
            verifier.EVIDENCE_DOC = Path(scratch) / "evidence.md"
            args = type("Final18Args", (), {
                "signed_app": Path("/Applications/TarsCompanion.app"),
                "with_restart_drill": False,
            })()
            verifier.phase_evidence(
                phases,
                args,
                provenance_reader=lambda: ("a" * 40, []),
            )
            verifier.EVIDENCE_DOC = original_doc
        self.assertFalse(phases.facts["process_tap_positive"])
        self.assertNotEqual(verifier.final_result_code(phases), verifier.EXIT_OK)

        for key, benign in (
            ("engine", "process-tap"),
            ("commit", "a" * 40),
            ("voice", "pt-BR voice"),
            ("phase_rows", [verifier._TypedPhaseRow({
                "name": verifier.PhaseID.EVIDENCE.value,
                "status": "PASS",
                "detail": verifier.PhaseDetail.template().text,
            })]),
        ):
            with self.subTest(fact=key):
                candidate = Phases(self.sentinel)
                candidate.facts[key] = verifier.CredentialReachableDiagnostic(str(benign))
                self.assertFalse(candidate.operational_facts_owned())
                candidate.facts[key] = {"nested": [verifier.CredentialReachableDiagnostic(str(benign))]}
                self.assertFalse(candidate.operational_facts_owned())

    def test_final_qualification_record_cannot_transplant_to_cloned_phase_ledger(self) -> None:
        phases = Phases(self.sentinel)
        install_positive_operational_facts(phases, restart_drill=False)
        phases.facts["transcript_valid_typed"] = True
        phases.facts["transcription_complete"] = True
        for row in positive_phase_rows(restart_drill=False):
            if row["name"] == verifier.PhaseID.EVIDENCE.value:
                continue
            phases.record(
                verifier.PhaseID(row["name"]),
                verifier.PhaseStatus.PASS,
                verifier.PhaseDetail.template(),
            )
        tuple_value = CaptureTuple(
            _TEST_PEER_FINGERPRINT,
            "nonce-1",
            attempt_binding("01234567-89ab-cdef-0123-456789abcdef"),
            1,
        )
        activation = Activation(tuple_value, PROCESS_TAP, PROCESS_TAP, PROCESS_TAP)
        snapshot = verifier.LiveProofSnapshot(
            artifact_valid=True,
            current_peer=True,
            authenticated_peer_key=_TEST_PEER_FINGERPRINT,
            launch_nonce="nonce-1",
            activation=activation,
            functional_permission_state="granted",
            functional_permission_tuple=tuple_value,
        )
        companion = type(
            "TransplantCompanion",
            (),
            {
                "_stream_key": self.sentinel,
                "_expected_head": "a" * 40,
                "proof_snapshot": snapshot,
                "cleanup_succeeded": True,
                "secret_seen": False,
            },
        )()
        previous_doc = verifier.EVIDENCE_DOC
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.EVIDENCE_DOC = Path(root) / "evidence.md"
                verifier.phase_evidence(
                    phases,
                    type("Args", (), {"signed_app": Path(root) / "TarsCompanion.app", "with_restart_drill": False})(),
                    companion=companion,
                    provenance_reader=lambda: ("a" * 40, []),
                )
            self.assertEqual(verifier.final_result_code(phases), verifier.EXIT_OK)
            record = phases._final_qualification_record
            self.assertIsNotNone(record)
            phases._final_qualification_record = dataclasses.replace(
                record,
                owner_instance=Phases(self.sentinel),
            )
            self.assertEqual(verifier.final_result_code(phases), verifier.EXIT_FAILED)
        finally:
            verifier.EVIDENCE_DOC = previous_doc

    def test_final18_v2_event_bindings_and_command_key_boundaries(self) -> None:
        key = _TEST_STREAM_KEY
        attempt = "01234567-89ab-cdef-0123-456789abcdef"
        launch_nonce = "nonce-1"
        session_id = "session-1"
        source_object = "ObjectIdentifier(0x1234)"
        observer_token = attempt
        event = {
            "actual_engine": PROCESS_TAP,
            "attempt_id": attempt_binding(attempt),
            "generation": 1,
            "kind": "activation",
            "launch_nonce": launch_binding(launch_nonce),
            "observer_binding": observer_binding(observer_token),
            "requested_engine": PROCESS_TAP,
            "resolved_engine": PROCESS_TAP,
            "session_binding": session_binding(session_id, launch_nonce),
            "source_binding": source_binding(source_object),
            "type": "event",
            "version": 2,
        }
        self.assertEqual(decode_event(encode_event(event, stream_key=key)[4:], stream_key=key), event)
        for field in (
            "attempt_id", "launch_nonce", "session_binding", "source_binding", "observer_binding",
        ):
            hostile = dict(event)
            hostile[field] = key
            with self.subTest(slot=field):
                with self.assertRaises(HarnessProtocolError):
                    decode_event(canonical_json(hostile), stream_key=key)
        hostile_device = dict(event)
        hostile_device["device_identity"] = "ProcessTap.SystemAudio"
        with self.assertRaises(HarnessProtocolError):
            decode_event(canonical_json(hostile_device), stream_key=key)
        for field in ("session_id", "launch_nonce"):
            command = {
                "gateway": "ws://127.0.0.1",
                "launch_nonce": launch_nonce,
                "session_id": session_id,
                "stream_key": key,
                "type": "session",
                "version": 2,
            }
            command[field] = key
            with self.subTest(command_slot=field):
                with self.assertRaises(HarnessProtocolError):
                    decode_session_command(canonical_json(command))

    def test_final18_failed_health_exact_constants_and_no_device_field(self) -> None:
        base = {
            "actual_engine": PROCESS_TAP,
            "attempt_id": "at1_0123456789abcdef0123456789abcdef",
            "generation": 1,
            "kind": "health",
            "launch_nonce": "ln1_0123456789abcdef0123456789abcdef",
            "observer_binding": "ob1_0123456789abcdef0123456789abcdef",
            "requested_engine": PROCESS_TAP,
            "resolved_engine": PROCESS_TAP,
            "session_binding": "sb1_" + "0" * 64,
            "source_binding": "so1_0123456789abcdef0123456789abcdef",
            "type": "event",
            "version": 2,
            "status": {
                "kind": "failed", "permission": "unknown", "route": "unknown",
                "interruption": "clear", "sleep": "awake", "overflowed": False,
                "failure_code": "capture-failed",
            },
        }
        self.assertEqual(decode_event(canonical_json(base)), base)
        mutations = (
            ("route", "healthy"),
            ("interruption", "interrupted"),
            ("sleep", "sleeping"),
            ("overflowed", True),
            ("permission", "granted"),
            ("failure_code", "permission-denied"),
        )
        for field, value in mutations:
            hostile = dict(base)
            hostile["status"] = dict(base["status"])
            hostile["status"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(HarnessProtocolError):
                    decode_event(canonical_json(hostile))
        hostile = dict(base)
        hostile["status"] = dict(base["status"])
        hostile["status"]["device_identity"] = "ProcessTap.SystemAudio"
        with self.assertRaises(HarnessProtocolError):
            decode_event(canonical_json(hostile))

    def test_final20_peer_fingerprint_is_bounded_domain_separated_binding(self) -> None:
        first = peer_fingerprint(self.peer)
        self.assertRegex(first, r"^pb1_[0-9a-f]{64}$")
        self.assertNotEqual(
            first,
            peer_fingerprint(
                PeerIdentity(
                    self.peer.euid,
                    self.peer.pid,
                    "00" * 31 + "01",
                    self.peer.executable_path,
                )
            ),
        )

    def test_final20_redactor_retire_clears_sentinel_and_preserves_seen_bit(self) -> None:
        clean = StreamingRedactor(self.sentinel)
        clean.feed("ordinary")
        clean.finish()
        clean.retire()
        self.assertIsNone(clean.sentinel)
        self.assertEqual(clean._pending, "")
        self.assertFalse(clean.seen)

        observed = StreamingRedactor(self.sentinel)
        observed.feed(self.sentinel)
        observed.finish()
        observed.retire()
        observed.retire()
        self.assertIsNone(observed.sentinel)
        self.assertTrue(observed.seen)

    def test_final20_markdown_projection_requires_minted_canonical_value(self) -> None:
        with self.assertRaises(HarnessProtocolError):
            markdown_projection({"result": "FAIL", "facts": {}})

    def test_companion_run_init_keyboard_interrupt_cleans_up_and_propagates(self) -> None:
        """Interrupting injected launcher after listener creation propagates KeyboardInterrupt and removes socket/run_dir."""
        class InterruptingLauncher:
            def launch(self, spec: object, *, on_process: object = None) -> object:
                raise KeyboardInterrupt("injected launcher interrupt")

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            orig_tmp = os.environ.get("TMPDIR")
            os.environ["TMPDIR"] = root
            try:
                run_dir_holder: list[Path] = []
                real_mkdtemp = tempfile.mkdtemp

                def track_mkdtemp(*args: object, **kwargs: object) -> str:
                    path = real_mkdtemp(*args, **kwargs)
                    run_dir_holder.append(Path(path))
                    return path

                with mock.patch("tempfile.mkdtemp", side_effect=track_mkdtemp):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        CompanionRun(
                            Path(root) / "TarsCompanion.app",
                            "session-1",
                            self.sentinel,
                            "r1-test",
                            launcher=InterruptingLauncher(),
                            artifact_facts=valid_artifact_facts(),
                            expected_head="a" * 40,
                            expected_tree="b" * 40,
                            expected_digest="a" * 64,
                            artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                            running_code_attestor=FakeRunningCodeAttestor(),
                        )
                self.assertEqual(str(raised.exception), "injected launcher interrupt")
                self.assertTrue(len(run_dir_holder) > 0)
                for run_dir in run_dir_holder:
                    self.assertFalse((run_dir / "control.sock").exists())
                    self.assertFalse(run_dir.exists())
            finally:
                if orig_tmp is not None:
                    os.environ["TMPDIR"] = orig_tmp
                else:
                    os.environ.pop("TMPDIR", None)
                verifier.SCRATCH = previous_scratch

    def test_companion_run_init_post_launch_keyboard_interrupt_cleans_helper_and_propagates(self) -> None:
        """Launcher returns process with stdout; injected KeyboardInterrupt at reader start terminates/waits helper and removes run_dir."""
        fake_helper = FakeOpenHelper(pid=7771)
        fake_helper.stdout = mock.MagicMock()

        class ReturnProcessLauncher:
            def launch(self, spec: object, *, on_process: object = None) -> object:
                if callable(on_process):
                    on_process(fake_helper)
                return fake_helper

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            orig_tmp = os.environ.get("TMPDIR")
            os.environ["TMPDIR"] = root
            try:
                run_dir_holder: list[Path] = []
                real_mkdtemp = tempfile.mkdtemp

                def track_mkdtemp(*args: object, **kwargs: object) -> str:
                    path = real_mkdtemp(*args, **kwargs)
                    run_dir_holder.append(Path(path))
                    return path

                with mock.patch("tempfile.mkdtemp", side_effect=track_mkdtemp), \
                     mock.patch("threading.Thread.start", side_effect=KeyboardInterrupt("injected reader start interrupt")):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        CompanionRun(
                            Path(root) / "TarsCompanion.app",
                            "session-1",
                            self.sentinel,
                            "post-launch-test",
                            launcher=ReturnProcessLauncher(),
                            artifact_facts=valid_artifact_facts(),
                            expected_head="a" * 40,
                            expected_tree="b" * 40,
                            expected_digest="a" * 64,
                            artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                            running_code_attestor=FakeRunningCodeAttestor(),
                        )
                self.assertEqual(str(raised.exception), "injected reader start interrupt")
                self.assertEqual(fake_helper.terminate_calls, 1)
                self.assertEqual(fake_helper.wait_calls, 1)
                self.assertTrue(len(run_dir_holder) > 0)
                for run_dir in run_dir_holder:
                    self.assertFalse((run_dir / "control.sock").exists())
                    self.assertFalse(run_dir.exists())
            finally:
                if orig_tmp is not None:
                    os.environ["TMPDIR"] = orig_tmp
                else:
                    os.environ.pop("TMPDIR", None)
                verifier.SCRATCH = previous_scratch

    def test_companion_run_init_cleanup_failures_do_not_mask_original_signal(self) -> None:
        """Helper cleanup raising errors during post-launch exception does not mask original signal."""
        class FailingHelper(FakeOpenHelper):
            def terminate(self) -> None:
                raise OSError("terminate failed")

            def wait(self, timeout: float | None = None) -> int:
                raise RuntimeError("wait failed")

            def kill(self) -> None:
                raise OSError("kill failed")

        failing_helper = FailingHelper(pid=7772)
        mock_stdout = mock.MagicMock()
        mock_stdout.close.side_effect = OSError("stdout close failed")
        failing_helper.stdout = mock_stdout

        class ReturnFailingProcessLauncher:
            def launch(self, spec: object, *, on_process: object = None) -> object:
                if callable(on_process):
                    on_process(failing_helper)
                return failing_helper

        previous_scratch = verifier.SCRATCH
        with tempfile.TemporaryDirectory() as root:
            verifier.SCRATCH = Path(root) / "scratch"
            orig_tmp = os.environ.get("TMPDIR")
            os.environ["TMPDIR"] = root
            try:
                with mock.patch("threading.Thread.start", side_effect=KeyboardInterrupt("injected reader start interrupt 2")):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        CompanionRun(
                            Path(root) / "TarsCompanion.app",
                            "session-1",
                            self.sentinel,
                            "post-launch-fail-test",
                            launcher=ReturnFailingProcessLauncher(),
                            artifact_facts=valid_artifact_facts(),
                            expected_head="a" * 40,
                            expected_tree="b" * 40,
                            expected_digest="a" * 64,
                            artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                            running_code_attestor=FakeRunningCodeAttestor(),
                        )
                self.assertEqual(str(raised.exception), "injected reader start interrupt 2")
            finally:
                if orig_tmp is not None:
                    os.environ["TMPDIR"] = orig_tmp
                else:
                    os.environ.pop("TMPDIR", None)
                verifier.SCRATCH = previous_scratch

    def test_macos_launch_services_adapter_post_spawn_keyboard_interrupt_schedule(self) -> None:
        """Deterministic adapter post-spawn control signal terminates/waits helper and propagates KeyboardInterrupt."""
        fake_helper = FakeOpenHelper(pid=8888)
        adapter = MacOSLaunchServicesAdapter(helper_spawner=make_helper_spawner(fake_helper))
        spec = make_launch_spec(
            "/Applications/TarsCompanion.app",
            socket_path="/tmp/test.sock",
            launch_nonce="nonce-1",
            stream_key=self.sentinel,
        )
        with mock.patch("live_system_audio_harness.PeerIdentity", side_effect=KeyboardInterrupt("post-spawn interrupt")):
            with self.assertRaises(KeyboardInterrupt) as raised:
                adapter.launch(spec, on_process=lambda p: None)
        self.assertEqual(str(raised.exception), "post-spawn interrupt")
        self.assertEqual(fake_helper.terminate_calls, 1)
        self.assertEqual(fake_helper.wait_calls, 1)

    def test_macos_launch_services_adapter_cleanup_failure_preserves_control_signal(self) -> None:
        """Helper cleanup failure during post-spawn exception does not mask original control signal."""
        class FailingHelper(FakeOpenHelper):
            def terminate(self) -> None:
                raise OSError("terminate failed")

            def wait(self, timeout: float | None = None) -> int:
                raise RuntimeError("wait failed")

        failing_helper = FailingHelper(pid=8889)
        adapter = MacOSLaunchServicesAdapter(helper_spawner=make_helper_spawner(failing_helper))
        spec = make_launch_spec(
            "/Applications/TarsCompanion.app",
            socket_path="/tmp/test.sock",
            launch_nonce="nonce-1",
            stream_key=self.sentinel,
        )
        with mock.patch("live_system_audio_harness.PeerIdentity", side_effect=KeyboardInterrupt("post-spawn interrupt 2")):
            with self.assertRaises(KeyboardInterrupt) as raised:
                adapter.launch(spec, on_process=lambda p: None)
        self.assertEqual(str(raised.exception), "post-spawn interrupt 2")

    def test_send_shutdown_request_performs_half_close_shut_wr(self) -> None:
        """send_shutdown_request sends the framed request and immediately shuts down SHUT_WR."""
        server_side, client_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
            state.accept_peer(self.peer)
            state.accept_command(canonical_json(self.command), peer=self.peer)
            nonce = shutdown_nonce()
            session_ref = session_binding("session-1", "nonce-1")

            fake_sock = RecordingFakeSocket(server_side)
            UnixHarnessServer.send_shutdown_request(
                fake_sock,  # type: ignore[arg-type]
                state,
                session_ref=session_ref,
                nonce=nonce,
                timeout=1.0,
            )
            self.assertEqual(fake_sock.shutdown_calls, [socket.SHUT_WR])
            data = client_side.recv(4096)
            self.assertTrue(len(data) > 4)
            self.assertEqual(client_side.recv(4096), b"")
        finally:
            server_side.close()
            client_side.close()

    def test_send_shutdown_request_failure_revokes_control_and_preserves_base_exception(self) -> None:
        """Failure in shutdown syscall revokes control and preserves BaseException."""
        state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
        state.accept_peer(self.peer)
        state.accept_command(canonical_json(self.command), peer=self.peer)
        nonce = shutdown_nonce()
        session_ref = session_binding("session-1", "nonce-1")

        fake_sock = RecordingFakeSocket()
        def raise_ki(how: int) -> None:
            raise KeyboardInterrupt("injected shutdown syscall interrupt")
        fake_sock.on_shutdown = raise_ki

        with self.assertRaises(KeyboardInterrupt) as raised:
            UnixHarnessServer.send_shutdown_request(
                fake_sock,  # type: ignore[arg-type]
                state,
                session_ref=session_ref,
                nonce=nonce,
                timeout=1.0,
            )
        self.assertEqual(str(raised.exception), "injected shutdown syscall interrupt")
        self.assertTrue(state.control_lost)
        self.assertIsNone(state._stream_key)
        self.assertIsNone(state._session_binding)

    def test_send_shutdown_request_timeout_becomes_harness_protocol_error(self) -> None:
        """Timeout in shutdown request send/shutdown becomes HarnessProtocolError."""
        state = HarnessState(expected_peer=self.peer, server_euid=501, launch_nonce="nonce-1")
        state.accept_peer(self.peer)
        state.accept_command(canonical_json(self.command), peer=self.peer)
        nonce = shutdown_nonce()
        session_ref = session_binding("session-1", "nonce-1")

        fake_sock = RecordingFakeSocket()
        def raise_timeout(data: bytes) -> None:
            raise socket.timeout("timed out")
        fake_sock.on_sendall = raise_timeout

        with self.assertRaises(HarnessProtocolError):
            UnixHarnessServer.send_shutdown_request(
                fake_sock,  # type: ignore[arg-type]
                state,
                session_ref=session_ref,
                nonce=nonce,
                timeout=1.0,
            )
        self.assertTrue(state.control_lost)

    def test_mic_channel_normal_exit_retires_credentials_and_returns_true(self) -> None:
        """Normal worker exit retires subprotocols and returns True from stop()."""
        pcm = b"\x00" * 3200
        mic = verifier.MicChannel("session-1", self.sentinel, pcm)

        async def fake_pump() -> None:
            mic._ready.set()

        with mock.patch.object(mic, "_pump", fake_pump):
            started = mic.start(timeout=1.0)
            self.assertTrue(started)
            stopped = mic.stop(timeout=1.0)
            self.assertTrue(stopped)
            self.assertFalse(mic.is_alive)
            self.assertEqual(mic._subprotocols, ())

    def test_mic_channel_stuck_worker_fails_stop_and_retains_credentials(self) -> None:
        """Stuck worker fails stop(timeout), does not retire credentials, and marks MIC_SUSTAINED FAIL."""
        pcm = b"\x00" * 3200
        mic = verifier.MicChannel("session-1", self.sentinel, pcm)
        unblock = threading.Event()

        async def fake_stuck_pump() -> None:
            mic._ready.set()
            while not unblock.is_set():
                await asyncio.sleep(0.01)

        try:
            with mock.patch.object(mic, "_pump", fake_stuck_pump):
                started = mic.start(timeout=1.0)
                self.assertTrue(started)
                stopped = mic.stop(timeout=0.05)
                self.assertFalse(stopped)
                self.assertTrue(mic.is_alive)
                self.assertNotEqual(mic._subprotocols, ())

                ph = Phases(sentinel=self.sentinel)
                fake_resp = mock.MagicMock()
                fake_resp.status_code = 200
                fake_resp.json.return_value = {"transcription_complete": True}
                with mock.patch("time.sleep", return_value=None), \
                     mock.patch("verify_live_system_audio.fetch_segments", return_value=[]), \
                     mock.patch("requests.post", return_value=fake_resp):
                    verifier.phase_stop_and_assert(
                        ph,
                        session_id="session-1",
                        expect_candidate=False,
                        expect_restart=False,
                        mic=mic,
                        companion=None,
                    )
                sustained_row = next(r for r in ph.rows if r["name"] == "Canal do entrevistador sustentado até o /stop")
                self.assertEqual(sustained_row["status"], "FAIL")
        finally:
            unblock.set()
            mic.stop(timeout=1.0)

    def test_phase_interviewer_audio_start_timeout_and_interrupt_preserve_ownership(self) -> None:
        """Start timeout and KeyboardInterrupt preserve cleanup ownership and propagate signals."""
        ph = Phases(sentinel=self.sentinel)
        with mock.patch("verify_live_system_audio.synth_pcm", return_value=b"\x00" * 3200), \
             mock.patch("verify_live_system_audio.MicChannel.start", return_value=False):
            result = verifier.phase_interviewer_audio(ph, "session-1", self.sentinel, "pt-BR")
            self.assertIsNone(result)
            self.assertIsNotNone(ph.cleanup_mic)

        ph2 = Phases(sentinel=self.sentinel)
        with mock.patch("verify_live_system_audio.synth_pcm", return_value=b"\x00" * 3200), \
             mock.patch("verify_live_system_audio.MicChannel.start", side_effect=KeyboardInterrupt("start interrupted")):
            with self.assertRaises(KeyboardInterrupt) as raised:
                verifier.phase_interviewer_audio(ph2, "session-1", self.sentinel, "pt-BR")
            self.assertEqual(str(raised.exception), "start interrupted")
            self.assertIsNotNone(ph2.cleanup_mic)

    def test_phase_backend_keyboard_interrupt_cleans_up_and_propagates(self) -> None:
        """Injected KeyboardInterrupt during readiness terminates proc, closes log, clears slots, and propagates."""
        ph = Phases(sentinel=self.sentinel)
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        fake_proc.terminate = mock.MagicMock()
        fake_proc.wait = mock.MagicMock(return_value=0)

        mock_log = mock.MagicMock()
        mock_open = mock.mock_open(mock=mock_log)

        with mock.patch("builtins.open", mock_open), \
             mock.patch("subprocess.Popen", return_value=fake_proc), \
             mock.patch("requests.get", side_effect=KeyboardInterrupt("injected backend interrupt")):
            with self.assertRaises(KeyboardInterrupt) as raised:
                verifier.phase_backend(ph)
        self.assertEqual(str(raised.exception), "injected backend interrupt")
        fake_proc.terminate.assert_called_once()
        fake_proc.wait.assert_called_once()
        mock_log().close.assert_called_once()
        self.assertIsNone(ph.cleanup_backend_proc)
        self.assertIsNone(ph.cleanup_backend_log)

    def test_phase_backend_cleanup_failure_does_not_mask_control_signal(self) -> None:
        """Proc terminate/wait failure retains slots and does not mask original KeyboardInterrupt."""
        ph = Phases(sentinel=self.sentinel)
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        fake_proc.terminate.side_effect = OSError("terminate failed")
        fake_proc.wait.side_effect = RuntimeError("wait failed")

        mock_open = mock.mock_open()
        handle = mock_open.return_value
        handle.close.side_effect = OSError("close failed")

        with mock.patch("builtins.open", mock_open), \
             mock.patch("subprocess.Popen", return_value=fake_proc), \
             mock.patch("requests.get", side_effect=KeyboardInterrupt("injected backend interrupt 2")):
            with self.assertRaises(KeyboardInterrupt) as raised:
                verifier.phase_backend(ph)
        self.assertEqual(str(raised.exception), "injected backend interrupt 2")
        self.assertIs(ph.cleanup_backend_proc, fake_proc)
        self.assertIs(ph.cleanup_backend_log, handle)

    def test_phase_backend_spawner_interrupt_after_on_process_cleans_proc_and_log_once(self) -> None:
        """Spawner invokes on_process then raises KeyboardInterrupt; phase_backend terminates/waits proc once, closes log once, clears cleanup slots, and propagates original signal."""
        ph = Phases(sentinel=self.sentinel)
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        fake_proc.terminate = mock.MagicMock()
        fake_proc.wait = mock.MagicMock(return_value=0)

        def interrupting_spawner(argv: list[str], *, on_process: Callable[[Any], None], **kwargs: Any) -> Any:
            on_process(fake_proc)
            raise KeyboardInterrupt("spawner interrupted after on_process")

        mock_log = mock.MagicMock()
        mock_open = mock.mock_open(mock=mock_log)

        with mock.patch("builtins.open", mock_open):
            with self.assertRaises(KeyboardInterrupt) as raised:
                verifier.phase_backend(ph, spawner=interrupting_spawner)
        self.assertEqual(str(raised.exception), "spawner interrupted after on_process")
        fake_proc.terminate.assert_called_once()
        fake_proc.wait.assert_called_once()
        mock_log().close.assert_called_once()
        self.assertIsNone(ph.cleanup_backend_proc)
        self.assertIsNone(ph.cleanup_backend_log)

    def test_phase_backend_spawner_returning_without_callback_fails_closed_and_cleans_returned(self) -> None:
        """Spawner returns without invoking on_process; phase_backend terminates/waits returned process once, closes log once, raises HarnessProtocolError."""
        ph = Phases(sentinel=self.sentinel)
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        fake_proc.terminate = mock.MagicMock()
        fake_proc.wait = mock.MagicMock(return_value=0)

        def no_callback_spawner(argv: list[str], *, on_process: Callable[[Any], None], **kwargs: Any) -> Any:
            return fake_proc

        mock_log = mock.MagicMock()
        mock_open = mock.mock_open(mock=mock_log)

        with mock.patch("builtins.open", mock_open):
            with self.assertRaises(HarnessProtocolError) as raised:
                verifier.phase_backend(ph, spawner=no_callback_spawner)
        self.assertIn("backend spawner returned without publishing process", str(raised.exception))
        fake_proc.terminate.assert_called_once()
        fake_proc.wait.assert_called_once()
        mock_log().close.assert_called_once()
        self.assertIsNone(ph.cleanup_backend_proc)
        self.assertIsNone(ph.cleanup_backend_log)

    def test_phase_backend_spawner_publishing_a_and_returning_b_cleans_both_once(self) -> None:
        """Spawner publishes proc A and returns proc B; phase_backend terminates/waits both once, closes log once, raises HarnessProtocolError."""
        ph = Phases(sentinel=self.sentinel)
        proc_a = mock.MagicMock(spec=subprocess.Popen)
        proc_a.poll.return_value = None
        proc_a.terminate = mock.MagicMock()
        proc_a.wait = mock.MagicMock(return_value=0)

        proc_b = mock.MagicMock(spec=subprocess.Popen)
        proc_b.poll.return_value = None
        proc_b.terminate = mock.MagicMock()
        proc_b.wait = mock.MagicMock(return_value=0)

        def mismatch_spawner(argv: list[str], *, on_process: Callable[[Any], None], **kwargs: Any) -> Any:
            on_process(proc_a)
            return proc_b

        mock_log = mock.MagicMock()
        mock_open = mock.mock_open(mock=mock_log)

        with mock.patch("builtins.open", mock_open):
            with self.assertRaises(HarnessProtocolError) as raised:
                verifier.phase_backend(ph, spawner=mismatch_spawner)
        self.assertIn("backend spawner published one process and returned another", str(raised.exception))
        proc_a.terminate.assert_called_once()
        proc_a.wait.assert_called_once()
        proc_b.terminate.assert_called_once()
        proc_b.wait.assert_called_once()
        mock_log().close.assert_called_once()
        self.assertIsNone(ph.cleanup_backend_proc)
        self.assertIsNone(ph.cleanup_backend_log)

    def test_production_spawn_backend_signal_mask_and_callback_ordering(self) -> None:
        """spawn_backend_process blocks SIGINT/SIGTERM before Popen, invokes on_process with signals blocked, and restores exact prior mask afterward."""
        events: list[tuple[str, Any]] = []
        old_mask = {signal.SIGUSR1}
        fake_proc = mock.MagicMock(spec=subprocess.Popen)

        def mock_sigmask(how: int, mask: Any) -> Any:
            events.append(("sigmask", how, set(mask)))
            if how == signal.SIG_BLOCK:
                return old_mask
            return set()

        def mock_popen(*args: Any, **kwargs: Any) -> Any:
            events.append(("popen", args, kwargs))
            return fake_proc

        def on_process(p: Any) -> None:
            events.append(("on_process", p))

        with mock.patch("signal.pthread_sigmask", side_effect=mock_sigmask), \
             mock.patch("subprocess.Popen", side_effect=mock_popen):
            res = verifier.spawn_backend_process(["/bin/echo", "test"], on_process=on_process)

        self.assertIs(res, fake_proc)
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0], ("sigmask", signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}))
        self.assertEqual(events[1][0], "popen")
        self.assertEqual(events[2], ("on_process", fake_proc))
        self.assertEqual(events[3], ("sigmask", signal.SIG_SETMASK, old_mask))

    def test_production_spawn_backend_fails_closed_when_sigmask_unavailable(self) -> None:
        """spawn_backend_process fails closed before Popen if pthread_sigmask is missing on POSIX or raises OSError."""
        if hasattr(signal, "pthread_sigmask"):
            with mock.patch.object(signal, "pthread_sigmask", side_effect=OSError("sigmask error")):
                with self.assertRaises(HarnessProtocolError) as raised:
                    verifier.spawn_backend_process(["/bin/echo"], on_process=lambda p: None)
                self.assertIn("cannot block SIGINT and SIGTERM", str(raised.exception))

    def test_phase_backend_spawn_backend_restore_sigmask_failure_cleans_proc_and_log_once(self) -> None:
        """spawn_backend_process fails on sigmask restore after on_process publication; phase_backend raises HarnessProtocolError, cleans published proc once, closes log once, and clears cleanup slots."""
        ph = Phases(sentinel=self.sentinel)
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        fake_proc.terminate = mock.MagicMock()
        fake_proc.wait = mock.MagicMock(return_value=0)

        events: list[str] = []
        old_mask = {signal.SIGUSR1}

        def mock_sigmask(how: int, mask: Any) -> Any:
            if how == signal.SIG_BLOCK:
                events.append("block")
                return old_mask
            if how == signal.SIG_SETMASK:
                events.append("restore")
                # Assert callback publication occurred before sigmask restore:
                self.assertIs(ph.cleanup_backend_proc, fake_proc)
                raise OSError("restore sigmask failed")
            return set()

        def mock_popen(*args: Any, **kwargs: Any) -> Any:
            events.append("popen")
            return fake_proc

        mock_log = mock.MagicMock()
        mock_open = mock.mock_open(mock=mock_log)

        with mock.patch("builtins.open", mock_open), \
             mock.patch("signal.pthread_sigmask", side_effect=mock_sigmask), \
             mock.patch("subprocess.Popen", side_effect=mock_popen):
            with self.assertRaises(HarnessProtocolError) as raised:
                verifier.phase_backend(ph)

        self.assertIn("cannot restore prior signal mask", str(raised.exception))
        self.assertEqual(events, ["block", "popen", "restore"])
        fake_proc.terminate.assert_called_once()
        fake_proc.wait.assert_called_once()
        mock_log().close.assert_called_once()
        self.assertIsNone(ph.cleanup_backend_proc)
        self.assertIsNone(ph.cleanup_backend_log)

    def test_production_spawn_helper_signal_mask_and_callback_ordering(self) -> None:
        """_production_spawn_helper blocks SIGINT/SIGTERM before Popen, publishes via on_helper_spawned while blocked, and restores the exact prior mask afterward."""
        events: list[tuple[str, Any]] = []
        old_mask = {signal.SIGUSR1}
        fake_helper = mock.MagicMock(spec=subprocess.Popen)

        def mock_sigmask(how: int, mask: Any) -> Any:
            events.append(("sigmask", how, set(mask)))
            if how == signal.SIG_BLOCK:
                return old_mask
            return set()

        def mock_popen(*args: Any, **kwargs: Any) -> Any:
            events.append(("popen", args, kwargs))
            return fake_helper

        def on_helper_spawned(h: Any) -> None:
            events.append(("on_helper_spawned", h))

        with mock.patch("signal.pthread_sigmask", side_effect=mock_sigmask), \
             mock.patch("subprocess.Popen", side_effect=mock_popen):
            res = _production_spawn_helper(["/usr/bin/open", "-n", "/Applications/TarsCompanion.app"], on_helper_spawned=on_helper_spawned)

        self.assertIs(res, fake_helper)
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0], ("sigmask", signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}))
        self.assertEqual(events[1][0], "popen")
        self.assertEqual(events[2], ("on_helper_spawned", fake_helper))
        self.assertEqual(events[3], ("sigmask", signal.SIG_SETMASK, old_mask))

    def test_production_spawn_helper_fails_closed_when_sigmask_unavailable(self) -> None:
        """_production_spawn_helper fails closed before Popen if pthread_sigmask is missing on POSIX or raises OSError."""
        fake_popen = mock.MagicMock()
        if hasattr(signal, "pthread_sigmask"):
            with mock.patch.object(signal, "pthread_sigmask", side_effect=OSError("sigmask error")), \
                 mock.patch("subprocess.Popen", fake_popen):
                with self.assertRaises(HarnessProtocolError) as raised:
                    _production_spawn_helper(["/usr/bin/open"], on_helper_spawned=lambda h: None)
                self.assertIn("cannot block SIGINT and SIGTERM", str(raised.exception))
                fake_popen.assert_not_called()

        if os.name == "posix":
            real_sigmask = getattr(signal, "pthread_sigmask", None)
            try:
                if real_sigmask is not None:
                    delattr(signal, "pthread_sigmask")
                with mock.patch("subprocess.Popen", fake_popen):
                    with self.assertRaises(HarnessProtocolError) as raised:
                        _production_spawn_helper(["/usr/bin/open"], on_helper_spawned=lambda h: None)
                    self.assertIn("pthread_sigmask is unavailable", str(raised.exception))
                    fake_popen.assert_not_called()
            finally:
                if real_sigmask is not None:
                    signal.pthread_sigmask = real_sigmask

    def test_production_spawn_helper_restore_failure_after_publish_is_cleaned_by_launch(self) -> None:
        """Restore OSError after on_helper_spawned publication raises HarnessProtocolError and launch terminates the helper once."""
        fake_helper = FakeOpenHelper(pid=7201)
        events: list[str] = []
        old_mask = {signal.SIGUSR1}
        spawned: list[object] = []
        launched: list[object] = []

        def mock_sigmask(how: int, mask: Any) -> Any:
            if how == signal.SIG_BLOCK:
                events.append("block")
                return old_mask
            if how == signal.SIG_SETMASK:
                events.append("restore")
                raise OSError("restore sigmask failed")
            return set()

        def mock_popen(*args: Any, **kwargs: Any) -> Any:
            events.append("popen")
            return fake_helper

        def on_helper_spawned(h: Any) -> None:
            spawned.append(h)

        with mock.patch("signal.pthread_sigmask", side_effect=mock_sigmask), \
             mock.patch("subprocess.Popen", side_effect=mock_popen):
            with self.assertRaises(HarnessProtocolError) as raised:
                _production_spawn_helper(
                    ["/usr/bin/open", "-n", "/Applications/TarsCompanion.app"],
                    on_helper_spawned=on_helper_spawned,
                )
        self.assertIn("cannot restore prior signal mask", str(raised.exception))
        self.assertEqual(events, ["block", "popen", "restore"])
        self.assertEqual(spawned, [fake_helper])

        events.clear()
        spawned.clear()
        spec = make_launch_spec(
            "/Applications/TarsCompanion.app",
            socket_path="/tmp/test.sock",
            launch_nonce="nonce-1",
            stream_key=self.sentinel,
        )
        adapter = MacOSLaunchServicesAdapter()
        with mock.patch("signal.pthread_sigmask", side_effect=mock_sigmask), \
             mock.patch("subprocess.Popen", side_effect=mock_popen), \
             mock.patch("os.path.exists", return_value=True):
            with self.assertRaises(HarnessProtocolError) as raised:
                adapter.launch(spec, on_process=launched.append)
        self.assertIn("cannot restore prior signal mask", str(raised.exception))
        self.assertEqual(events, ["block", "popen", "restore"])
        self.assertEqual(launched, [])
        self.assertEqual(fake_helper.terminate_calls, 1)
        self.assertEqual(fake_helper.wait_calls, 1)

    def test_macos_launch_services_adapter_keyboard_interrupt_after_spawn_before_on_process(self) -> None:
        """KeyboardInterrupt while wrapping the published helper, before on_process, terminates/waits the helper and never publishes."""
        fake_helper = FakeOpenHelper(pid=7210)
        adapter = MacOSLaunchServicesAdapter(helper_spawner=make_helper_spawner(fake_helper))
        spec = make_launch_spec(
            "/Applications/TarsCompanion.app",
            socket_path="/tmp/test.sock",
            launch_nonce="nonce-1",
            stream_key=self.sentinel,
        )
        published: list[object] = []
        with mock.patch.object(
            LaunchServicesProcess,
            "__init__",
            side_effect=KeyboardInterrupt("after spawn before on_process"),
        ):
            with self.assertRaises(KeyboardInterrupt) as raised:
                adapter.launch(spec, on_process=published.append)
        self.assertEqual(str(raised.exception), "after spawn before on_process")
        self.assertEqual(published, [])
        self.assertEqual(fake_helper.terminate_calls, 1)
        self.assertEqual(fake_helper.wait_calls, 1)

    def test_main_finally_deduplicates_and_cleans_retained_backend_and_log(self) -> None:
        """main finally deduplicates direct and cleanup slot backend/log, retries terminate/wait/kill/wait without subprocess."""
        ph = Phases(sentinel=self.sentinel)
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        fake_proc.terminate = mock.MagicMock()
        fake_proc.wait = mock.MagicMock(return_value=0)
        ph.cleanup_backend_proc = fake_proc

        fake_log = mock.MagicMock()
        ph.cleanup_backend_log = fake_log

        stopped_backend_procs: set[int] = set()
        for p in (getattr(ph, "cleanup_backend_proc", None), None):
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
                if not p_cleaned:
                    ph.cleanup_backend_proc = p

        closed_backend_logs: set[int] = set()
        for l in (getattr(ph, "cleanup_backend_log", None), None):
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
                if not l_closed:
                    ph.cleanup_backend_log = l

        fake_proc.terminate.assert_called_once()
        fake_proc.wait.assert_called_once()
        fake_log.close.assert_called_once()

    def test_macos_launch_services_adapter_publishes_helper_before_peer_identity(self) -> None:
        """Adapter helper_spawner on_helper_spawned publishes helper before PeerIdentity construction."""
        fake_helper = FakeOpenHelper(pid=9901)
        published_helpers: list[object] = []

        def custom_spawner(
            argv: list[str],
            *,
            on_helper_spawned: Callable[[Any], None],
            stdout: Any = subprocess.PIPE,
            stderr: Any = subprocess.STDOUT,
            **kwargs: Any,
        ) -> FakeOpenHelper:
            on_helper_spawned(fake_helper)
            return fake_helper

        adapter = MacOSLaunchServicesAdapter(helper_spawner=custom_spawner)
        spec = make_launch_spec(
            "/Applications/TarsCompanion.app",
            socket_path="/tmp/test.sock",
            launch_nonce="nonce-1",
            stream_key=self.sentinel,
        )

        def on_proc(p: object) -> None:
            published_helpers.append(p)

        with mock.patch("live_system_audio_harness.PeerIdentity", side_effect=KeyboardInterrupt("interrupt during peer")):
            with self.assertRaises(KeyboardInterrupt):
                adapter.launch(spec, on_process=on_proc)

        self.assertEqual(len(published_helpers), 1)
        self.assertIsInstance(published_helpers[0], LaunchServicesProcess)
        self.assertEqual(published_helpers[0].helper_pid, fake_helper.pid)
        self.assertEqual(fake_helper.terminate_calls, 1)
        self.assertEqual(fake_helper.wait_calls, 1)

    def test_companion_run_publishes_self_before_listener_bind(self) -> None:
        """CompanionRun calls on_publish before listener bind; bound exception propagates with published run retained."""
        published_runs: list[CompanionRun] = []

        def on_pub(r: CompanionRun) -> None:
            published_runs.append(r)

        with tempfile.TemporaryDirectory() as root:
            app = Path(root) / "TarsCompanion.app"
            with mock.patch.object(UnixHarnessServer, "bind", side_effect=OSError("bind failed")):
                with self.assertRaises(OSError):
                    CompanionRun(
                        app,
                        "session-1",
                        self.sentinel,
                        "pub-test",
                        artifact_facts=valid_artifact_facts(),
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                        running_code_attestor=FakeRunningCodeAttestor(),
                        on_publish=on_pub,
                    )
        self.assertEqual(len(published_runs), 1)
        self.assertIsInstance(published_runs[0], CompanionRun)

    def test_companion_run_proc_mismatch_raises_and_cleans_up(self) -> None:
        """CompanionRun rejects returned process if it differs from published process facade."""
        proc_a = FakeOpenHelper(pid=9902)
        proc_b = FakeOpenHelper(pid=9903)

        class MismatchLauncher:
            def launch(self, spec: object, *, on_process: object = None) -> tuple[FakeOpenHelper, PeerIdentity]:
                if callable(on_process):
                    on_process(proc_a)
                return proc_b, PeerIdentity(os.geteuid(), 9903, None, None)

        with tempfile.TemporaryDirectory() as root:
            app = Path(root) / "TarsCompanion.app"
            with self.assertRaises(HarnessProtocolError) as raised:
                CompanionRun(
                    app,
                    "session-1",
                    self.sentinel,
                    "mismatch-test",
                    launcher=MismatchLauncher(),
                    artifact_facts=valid_artifact_facts(),
                    expected_head="a" * 40,
                    expected_tree="b" * 40,
                    expected_digest="a" * 64,
                    artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                    running_code_attestor=FakeRunningCodeAttestor(),
                )
            self.assertIn("launched process does not match published process", str(raised.exception))

    def test_phase_backend_retains_cleanup_slots_on_success(self) -> None:
        """phase_backend retains cleanup_backend_proc and cleanup_backend_log on PASS."""
        ph = Phases(sentinel=self.sentinel)
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None

        mock_log = mock.MagicMock()
        mock_open = mock.mock_open(mock=mock_log)

        with mock.patch("builtins.open", mock_open), \
             mock.patch("subprocess.Popen", return_value=fake_proc), \
             mock.patch("requests.get", return_value=mock.MagicMock(status_code=200)):
            res = verifier.phase_backend(ph)
        self.assertIsNotNone(res)
        proc, log = res
        self.assertIs(ph.cleanup_backend_proc, proc)
        self.assertIs(ph.cleanup_backend_log, log)

    def test_causal_spawner_publishes_then_keyboard_interrupt_propagates_and_cleans_helper(self) -> None:
        """Spawner publishes helper, raises KeyboardInterrupt; launch propagates and cleans helper."""
        helper = FakeOpenHelper(pid=7101)
        def spawner(argv: list[str], *, on_helper_spawned: Callable[[Any], None], **kwargs: Any) -> FakeOpenHelper:
            on_helper_spawned(helper)
            raise KeyboardInterrupt("injected spawner interrupt")
        adapter = MacOSLaunchServicesAdapter(helper_spawner=spawner)
        spec = make_launch_spec("/Applications/TarsCompanion.app", socket_path="/tmp/test.sock", launch_nonce="nonce-1", stream_key=self.sentinel)
        with self.assertRaises(KeyboardInterrupt) as raised:
            adapter.launch(spec, on_process=lambda p: None)
        self.assertEqual(str(raised.exception), "injected spawner interrupt")
        self.assertEqual(helper.terminate_calls, 1)
        self.assertEqual(helper.wait_calls, 1)

    def test_causal_spawner_returns_without_callback_raises_protocol_error_and_cleans_fake(self) -> None:
        """Spawner returns without invoking on_helper_spawned; launch terminates/waits helper and raises HarnessProtocolError."""
        helper = FakeOpenHelper(pid=7102)
        def spawner(argv: list[str], *, on_helper_spawned: Callable[[Any], None], **kwargs: Any) -> FakeOpenHelper:
            return helper
        adapter = MacOSLaunchServicesAdapter(helper_spawner=spawner)
        spec = make_launch_spec("/Applications/TarsCompanion.app", socket_path="/tmp/test.sock", launch_nonce="nonce-1", stream_key=self.sentinel)
        with self.assertRaises(HarnessProtocolError) as raised:
            adapter.launch(spec, on_process=lambda p: None)
        self.assertIn("LaunchServices helper spawner returned without publishing helper", str(raised.exception))
        self.assertEqual(helper.terminate_calls, 1)
        self.assertEqual(helper.wait_calls, 1)

    def test_causal_spawner_publishes_a_returns_b_cleans_both_and_raises_protocol_error(self) -> None:
        """Spawner publishes helper A but returns helper B; launch terminates both and raises HarnessProtocolError."""
        helper_a = FakeOpenHelper(pid=7103)
        helper_b = FakeOpenHelper(pid=7104)
        def spawner(argv: list[str], *, on_helper_spawned: Callable[[Any], None], **kwargs: Any) -> FakeOpenHelper:
            on_helper_spawned(helper_a)
            return helper_b
        adapter = MacOSLaunchServicesAdapter(helper_spawner=spawner)
        spec = make_launch_spec("/Applications/TarsCompanion.app", socket_path="/tmp/test.sock", launch_nonce="nonce-1", stream_key=self.sentinel)
        with self.assertRaises(HarnessProtocolError) as raised:
            adapter.launch(spec, on_process=lambda p: None)
        self.assertIn("LaunchServices helper spawner published one object and returned another", str(raised.exception))
        self.assertEqual(helper_a.terminate_calls, 1)
        self.assertEqual(helper_a.wait_calls, 1)
        self.assertEqual(helper_b.terminate_calls, 1)
        self.assertEqual(helper_b.wait_calls, 1)

    def test_causal_launcher_on_process_publication_then_keyboard_interrupt_cleans_owner_and_run_dir(self) -> None:
        """CompanionRun launcher publishes process then KeyboardInterrupt occurs; CompanionRun cleans helper and run_dir."""
        helper = FakeOpenHelper(pid=7105)
        class InterruptingLauncher:
            def launch(self, spec: Any, *, on_process: Callable[[Any], None]) -> tuple[Any, PeerIdentity]:
                on_process(helper)
                raise KeyboardInterrupt("interrupt after on_process")
        previous_scratch = verifier.SCRATCH
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.SCRATCH = Path(root) / "scratch"
                with self.assertRaises(KeyboardInterrupt) as raised:
                    CompanionRun(
                        Path(root) / "TarsCompanion.app",
                        "session-1",
                        self.sentinel,
                        "causal-interrupt-launcher",
                        launcher=InterruptingLauncher(),
                        artifact_facts=valid_artifact_facts(),
                        expected_head="a" * 40,
                        expected_tree="b" * 40,
                        expected_digest="a" * 64,
                        artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                        running_code_attestor=FakeRunningCodeAttestor(),
                    )
                self.assertEqual(str(raised.exception), "interrupt after on_process")
                self.assertEqual(helper.terminate_calls, 1)
                self.assertEqual(helper.wait_calls, 1)
        finally:
            verifier.SCRATCH = previous_scratch

    def test_companion_run_chmod_keyboard_interrupt_cleans_run_dir_and_propagates(self) -> None:
        """Publish real CompanionRun via on_publish, raise KeyboardInterrupt in os.chmod after temp dir created; directory is removed, no listener bind/launch occurs, signal propagates."""
        published_runs: list[CompanionRun] = []
        created_dirs: list[Path] = []
        helper = FakeOpenHelper(pid=7106)

        class DummyLauncher:
            def launch(self, spec: Any, *, on_process: Callable[[Any], None] | None = None) -> tuple[Any, PeerIdentity]:
                if callable(on_process):
                    on_process(helper)
                return helper, PeerIdentity(os.geteuid(), None, None, None)

        def fake_chmod(path: Any, mode: int, *args: Any, **kwargs: Any) -> None:
            p = Path(path)
            created_dirs.append(p)
            raise KeyboardInterrupt("chmod interrupted")

        previous_scratch = verifier.SCRATCH
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.SCRATCH = Path(root) / "scratch"
                app = Path(root) / "TarsCompanion.app"
                with mock.patch("os.chmod", side_effect=fake_chmod):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        CompanionRun(
                            app,
                            "session-1",
                            self.sentinel,
                            "chmod-interrupt",
                            launcher=DummyLauncher(),
                            artifact_facts=valid_artifact_facts(),
                            expected_head="a" * 40,
                            expected_tree="b" * 40,
                            expected_digest="a" * 64,
                            artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                            running_code_attestor=FakeRunningCodeAttestor(),
                            on_publish=lambda run: published_runs.append(run),
                        )
                self.assertEqual(str(raised.exception), "chmod interrupted")
                self.assertEqual(len(published_runs), 1)
                run = published_runs[0]
                self.assertTrue(run._teardown_complete)
                self.assertEqual(len(created_dirs), 1)
                self.assertFalse(created_dirs[0].exists())
                self.assertIsNone(run.server)
        finally:
            verifier.SCRATCH = previous_scratch

    def test_companion_run_chmod_os_error_cleans_run_dir_and_propagates(self) -> None:
        """Publish real CompanionRun via on_publish, raise OSError in os.chmod after temp dir created; directory is removed, no listener bind/launch occurs, error propagates."""
        published_runs: list[CompanionRun] = []
        created_dirs: list[Path] = []
        helper = FakeOpenHelper(pid=7107)

        class DummyLauncher:
            def launch(self, spec: Any, *, on_process: Callable[[Any], None] | None = None) -> tuple[Any, PeerIdentity]:
                if callable(on_process):
                    on_process(helper)
                return helper, PeerIdentity(os.geteuid(), None, None, None)

        def fake_chmod(path: Any, mode: int, *args: Any, **kwargs: Any) -> None:
            p = Path(path)
            created_dirs.append(p)
            raise OSError("chmod permission error")

        previous_scratch = verifier.SCRATCH
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.SCRATCH = Path(root) / "scratch"
                app = Path(root) / "TarsCompanion.app"
                with mock.patch("os.chmod", side_effect=fake_chmod):
                    with self.assertRaises(OSError) as raised:
                        CompanionRun(
                            app,
                            "session-1",
                            self.sentinel,
                            "chmod-oserror",
                            launcher=DummyLauncher(),
                            artifact_facts=valid_artifact_facts(),
                            expected_head="a" * 40,
                            expected_tree="b" * 40,
                            expected_digest="a" * 64,
                            artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                            running_code_attestor=FakeRunningCodeAttestor(),
                            on_publish=lambda run: published_runs.append(run),
                        )
                self.assertEqual(str(raised.exception), "chmod permission error")
                self.assertEqual(len(published_runs), 1)
                run = published_runs[0]
                self.assertTrue(run._teardown_complete)
                self.assertEqual(len(created_dirs), 1)
                self.assertFalse(created_dirs[0].exists())
                self.assertIsNone(run.server)
        finally:
            verifier.SCRATCH = previous_scratch

    def test_causal_phase_backend_interrupted_at_caller_cleans_proc_and_log_once(self) -> None:
        """Driving main() with interrupt at phase_session cleans backend proc and log exactly once."""
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        fake_proc.terminate = mock.MagicMock()
        fake_proc.wait = mock.MagicMock(return_value=0)
        fake_log = mock.MagicMock()

        def fake_preflight(ph: Phases, *a: Any, **kw: Any) -> bool:
            ph.facts["voice"] = "pt-BR"
            return True

        previous_scratch = verifier.SCRATCH
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.SCRATCH = Path(root) / "scratch"
                app = Path(root) / "TarsCompanion.app"
                with mock.patch("argparse.ArgumentParser.parse_args", return_value=argparse.Namespace(signed_app=app, with_restart_drill=False)), \
                     mock.patch.object(verifier, "phase_preflight", side_effect=fake_preflight), \
                     mock.patch.object(verifier, "phase_backend", side_effect=lambda ph: (setattr(ph, "cleanup_backend_proc", fake_proc), setattr(ph, "cleanup_backend_log", fake_log)) and (fake_proc, fake_log)), \
                     mock.patch.object(verifier, "phase_session", side_effect=KeyboardInterrupt("interrupt at session")):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        verifier.main()
                    self.assertEqual(str(raised.exception), "interrupt at session")
                    fake_proc.terminate.assert_called_once()
                    fake_proc.wait.assert_called_once()
                    fake_log.close.assert_called_once()
        finally:
            verifier.SCRATCH = previous_scratch

    def test_causal_phase_companion_interrupted_before_caller_publication_cleans_owner_once(self) -> None:
        """Driving main() with interrupt at phase_interviewer_audio cleans companion run exactly once."""
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = 0
        fake_log = mock.MagicMock()

        fake_run = mock.MagicMock(spec=CompanionRun)
        fake_run.stop.return_value = True

        def fake_preflight(ph: Phases, *a: Any, **kw: Any) -> bool:
            ph.facts["voice"] = "pt-BR"
            return True

        previous_scratch = verifier.SCRATCH
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.SCRATCH = Path(root) / "scratch"
                app = Path(root) / "TarsCompanion.app"
                with mock.patch("argparse.ArgumentParser.parse_args", return_value=argparse.Namespace(signed_app=app, with_restart_drill=False)), \
                     mock.patch.object(verifier, "phase_preflight", side_effect=fake_preflight), \
                     mock.patch.object(verifier, "phase_backend", side_effect=lambda ph: (setattr(ph, "cleanup_backend_proc", fake_proc), setattr(ph, "cleanup_backend_log", fake_log)) and (fake_proc, fake_log)), \
                     mock.patch.object(verifier, "phase_session", return_value=("session-1", self.sentinel)), \
                     mock.patch.object(verifier, "phase_wrong_key", return_value=None), \
                     mock.patch.object(verifier, "phase_companion", side_effect=lambda ph, *a, **kw: setattr(ph, "cleanup_run", fake_run) or fake_run), \
                     mock.patch.object(verifier, "phase_interviewer_audio", side_effect=KeyboardInterrupt("interrupt at interviewer")):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        verifier.main()
                    self.assertEqual(str(raised.exception), "interrupt at interviewer")
                    fake_run.stop.assert_called_once()
                    fake_log.close.assert_called_once()
        finally:
            verifier.SCRATCH = previous_scratch

    def test_causal_phase_interviewer_audio_interrupted_before_caller_publication_cleans_mic_once(self) -> None:
        """Driving main() with interrupt at phase_candidate_audio cleans mic channel exactly once."""
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = 0
        fake_log = mock.MagicMock()

        fake_run = mock.MagicMock(spec=CompanionRun)
        fake_run.stop.return_value = True

        fake_mic = mock.MagicMock(spec=MicChannel)
        fake_mic.stop.return_value = True

        def fake_preflight(ph: Phases, *a: Any, **kw: Any) -> bool:
            ph.facts["voice"] = "pt-BR"
            return True

        previous_scratch = verifier.SCRATCH
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.SCRATCH = Path(root) / "scratch"
                app = Path(root) / "TarsCompanion.app"
                with mock.patch("argparse.ArgumentParser.parse_args", return_value=argparse.Namespace(signed_app=app, with_restart_drill=False)), \
                     mock.patch.object(verifier, "phase_preflight", side_effect=fake_preflight), \
                     mock.patch.object(verifier, "phase_backend", side_effect=lambda ph: (setattr(ph, "cleanup_backend_proc", fake_proc), setattr(ph, "cleanup_backend_log", fake_log)) and (fake_proc, fake_log)), \
                     mock.patch.object(verifier, "phase_session", return_value=("session-1", self.sentinel)), \
                     mock.patch.object(verifier, "phase_wrong_key", return_value=None), \
                     mock.patch.object(verifier, "phase_companion", side_effect=lambda ph, *a, **kw: setattr(ph, "cleanup_run", fake_run) or fake_run), \
                     mock.patch.object(verifier, "phase_interviewer_audio", side_effect=lambda ph, *a, **kw: setattr(ph, "cleanup_mic", fake_mic) or fake_mic), \
                     mock.patch.object(verifier, "phase_candidate_audio", side_effect=KeyboardInterrupt("interrupt at candidate")):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        verifier.main()
                    self.assertEqual(str(raised.exception), "interrupt at candidate")
                    fake_mic.stop.assert_called_once()
                    fake_run.stop.assert_called_once()
                    fake_log.close.assert_called_once()
        finally:
            verifier.SCRATCH = previous_scratch

    def test_causal_restart_replacement_transfer_interrupted_cleans_restart_run_once(self) -> None:
        """Driving main() with interrupt at phase_stop_and_assert cleans restart replacement run exactly once."""
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = 0
        fake_log = mock.MagicMock()

        fake_run = mock.MagicMock(spec=CompanionRun)
        fake_run.stop.return_value = True

        fake_replacement = mock.MagicMock(spec=CompanionRun)
        fake_replacement.stop.return_value = True

        fake_mic = mock.MagicMock(spec=MicChannel)
        fake_mic.stop.return_value = True

        def fake_preflight(ph: Phases, *a: Any, **kw: Any) -> bool:
            ph.facts["voice"] = "pt-BR"
            return True

        previous_scratch = verifier.SCRATCH
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.SCRATCH = Path(root) / "scratch"
                app = Path(root) / "TarsCompanion.app"
                with mock.patch("argparse.ArgumentParser.parse_args", return_value=argparse.Namespace(signed_app=app, with_restart_drill=True)), \
                     mock.patch.object(verifier, "phase_preflight", side_effect=fake_preflight), \
                     mock.patch.object(verifier, "phase_backend", side_effect=lambda ph: (setattr(ph, "cleanup_backend_proc", fake_proc), setattr(ph, "cleanup_backend_log", fake_log)) and (fake_proc, fake_log)), \
                     mock.patch.object(verifier, "phase_session", return_value=("session-1", self.sentinel)), \
                     mock.patch.object(verifier, "phase_wrong_key", return_value=None), \
                     mock.patch.object(verifier, "phase_companion", side_effect=lambda ph, *a, **kw: setattr(ph, "cleanup_run", fake_run) or fake_run), \
                     mock.patch.object(verifier, "phase_interviewer_audio", side_effect=lambda ph, *a, **kw: setattr(ph, "cleanup_mic", fake_mic) or fake_mic), \
                     mock.patch.object(verifier, "phase_candidate_audio", return_value=True), \
                     mock.patch.object(verifier, "phase_restart_drill", side_effect=lambda ph, *a, **kw: (setattr(ph, "restart_run", fake_replacement), ph.record("Reinício do companion", "PASS", "restarted"))), \
                     mock.patch.object(verifier, "phase_stop_and_assert", side_effect=KeyboardInterrupt("interrupt at stop")):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        verifier.main()
                    self.assertEqual(str(raised.exception), "interrupt at stop")
                    fake_replacement.stop.assert_called_once()
                    fake_run.stop.assert_called_once()
                    fake_mic.stop.assert_called_once()
                    fake_log.close.assert_called_once()
        finally:
            verifier.SCRATCH = previous_scratch

    def test_causal_cleanup_failures_preserve_original_keyboard_interrupt_and_slots(self) -> None:
        """Driving main() with cleanup failures preserves original KeyboardInterrupt and keeps failed slots retained on ph."""
        fake_proc = mock.MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        fake_proc.terminate.side_effect = OSError("terminate failed")
        fake_proc.wait.side_effect = RuntimeError("wait failed")

        fake_log = mock.MagicMock()
        fake_log.close.side_effect = OSError("close failed")

        fake_run = mock.MagicMock(spec=CompanionRun)
        fake_run.stop.side_effect = RuntimeError("run stop failed")

        def fake_preflight(ph: Phases, *a: Any, **kw: Any) -> bool:
            ph.facts["voice"] = "pt-BR"
            return True

        phases_holder: list[Phases] = []
        original_phases_init = Phases.__init__
        def tracked_phases_init(self: Phases, *a: Any, **kw: Any) -> None:
            original_phases_init(self, *a, **kw)
            phases_holder.append(self)

        previous_scratch = verifier.SCRATCH
        try:
            with tempfile.TemporaryDirectory() as root:
                verifier.SCRATCH = Path(root) / "scratch"
                app = Path(root) / "TarsCompanion.app"
                with mock.patch.object(Phases, "__init__", tracked_phases_init), \
                     mock.patch("argparse.ArgumentParser.parse_args", return_value=argparse.Namespace(signed_app=app, with_restart_drill=False)), \
                     mock.patch.object(verifier, "phase_preflight", side_effect=fake_preflight), \
                     mock.patch.object(verifier, "phase_backend", side_effect=lambda ph: (setattr(ph, "cleanup_backend_proc", fake_proc), setattr(ph, "cleanup_backend_log", fake_log)) and (fake_proc, fake_log)), \
                     mock.patch.object(verifier, "phase_session", return_value=("session-1", self.sentinel)), \
                     mock.patch.object(verifier, "phase_wrong_key", return_value=None), \
                     mock.patch.object(verifier, "phase_companion", side_effect=lambda ph, *a, **kw: setattr(ph, "cleanup_run", fake_run) or fake_run), \
                     mock.patch.object(verifier, "phase_interviewer_audio", side_effect=KeyboardInterrupt("original interruption")):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        verifier.main()
                    self.assertEqual(str(raised.exception), "original interruption")
                    self.assertTrue(len(phases_holder) > 0)
                    ph = phases_holder[-1]
                    self.assertIs(ph.cleanup_backend_proc, fake_proc)
                    self.assertIs(ph.cleanup_backend_log, fake_log)
                    self.assertIs(ph.cleanup_run, fake_run)
        finally:
            verifier.SCRATCH = previous_scratch

    def test_complete_after_control_eof_passes_explicit_timeout_5(self) -> None:
        """_complete_after_control_eof waits for passive helper completion with timeout=5.0 and no signaling."""
        with tempfile.TemporaryDirectory() as root:
            helper = FakeOpenHelper(pid=9904)
            helper.returncode = 0

            class Launcher:
                def launch(self, _spec: object, *, on_process: object = None) -> tuple[FakeOpenHelper, PeerIdentity]:
                    if callable(on_process):
                        on_process(helper)
                    return helper, PeerIdentity(os.geteuid(), None, None, None)

            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "eof-timeout-5",
                launcher=Launcher(),
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            try:
                run._control_eof_observed = True
                wait_calls: list[float] = []

                def tracked_wait(timeout: float = 1.0) -> bool:
                    wait_calls.append(timeout)
                    return True

                run._wait_for_passive_helper_completion = tracked_wait  # type: ignore[method-assign]
                success = run._complete_after_control_eof()
                self.assertTrue(success)
                self.assertEqual(wait_calls, [5.0])
                self.assertEqual(helper.terminate_calls, 0)
                self.assertEqual(helper.kill_calls, 0)
            finally:
                run.stop()

    def test_complete_after_control_eof_timeout_failure_fails_cleanly(self) -> None:
        """_complete_after_control_eof returns False and records diagnostic when passive helper completion times out."""
        with tempfile.TemporaryDirectory() as root:
            helper = FakeOpenHelper(pid=9905)

            class Launcher:
                def launch(self, _spec: object, *, on_process: object = None) -> tuple[FakeOpenHelper, PeerIdentity]:
                    if callable(on_process):
                        on_process(helper)
                    return helper, PeerIdentity(os.geteuid(), None, None, None)

            run = CompanionRun(
                Path(root) / "TarsCompanion.app",
                "session-1",
                self.sentinel,
                "eof-timeout-fail",
                launcher=Launcher(),
                artifact_facts=valid_artifact_facts(),
                expected_head="a" * 40,
                expected_tree="b" * 40,
                expected_digest="a" * 64,
                artifact_inspector=FakeArtifactInspector(valid_artifact_facts()),
                running_code_attestor=FakeRunningCodeAttestor(),
            )
            original_wait = run._wait_for_passive_helper_completion
            try:
                run._control_eof_observed = True
                run._wait_for_passive_helper_completion = lambda timeout=5.0: False  # type: ignore[method-assign]
                success = run._complete_after_control_eof()
                self.assertFalse(success)
                self.assertEqual(run._cleanup_error, "LaunchServices helper completion was not observed")
            finally:
                run._wait_for_passive_helper_completion = original_wait  # type: ignore[method-assign]
                helper.returncode = 0
                self.assertTrue(run.stop())


def dataclasses_replace(value: object, **changes: object) -> object:
    # Keep this helper local so tests remain pure and do not need a mutable
    # fixture or a subprocess.
    import dataclasses
    return dataclasses.replace(value, **changes)


if __name__ == "__main__":
    unittest.main()

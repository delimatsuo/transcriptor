"""Negative and determinism tests for the guard-first boundary."""

import errno
import importlib
import json
import os
import socket
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path

from tars_phase1a.fixtures import (
    COMMITTED_MANIFEST_SHA256,
    FixtureCatalog,
    load_committed_catalog,
)
from tars_phase1a.guard import GuardViolation, validate_environment


PROTOCOL_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROTOCOL_ROOT / "fixtures" / "phase1a-v1.manifest.json"
SCHEMA_PATH = PROTOCOL_ROOT / "schema" / "protocol-v1.schema.json"


class EnvironmentGuardTests(unittest.TestCase):
    def test_scrubbed_process_environment_is_valid(self) -> None:
        validate_environment()
        self.assertEqual(os.environ["TARS_PHASE1A_MODE"], "offline")
        self.assertEqual(os.environ["HOME"], "/var/empty")

    def test_credential_project_endpoint_and_proxy_keys_fail_closed(self) -> None:
        forbidden = (
            "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
            "FIRESTORE_EMULATOR_HOST",
            "GCE_METADATA_HOST",
            "GCP_PROJECT",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "FIREBASE_CONFIG",
            "TARS_GATEWAY_ENDPOINT",
            "TARS_GATEWAY_URL",
            "HTTPS_PROXY",
            "SOME_API_KEY",
        )
        for key in forbidden:
            with self.subTest(key=key):
                environment = {"TARS_PHASE1A_MODE": "offline", key: "sentinel"}
                with self.assertRaises(GuardViolation):
                    validate_environment(environment)

    def test_offline_marker_is_mandatory(self) -> None:
        with self.assertRaises(GuardViolation):
            validate_environment({})


class ImportAndMutationGuardTests(unittest.TestCase):
    def test_production_and_cloud_imports_are_blocked(self) -> None:
        for module_name in (
            "backend.main",
            "ctypes",
            "google.cloud.speech",
            "firebase_admin",
        ):
            with self.subTest(module_name=module_name):
                with self.assertRaises(GuardViolation):
                    importlib.import_module(module_name)

    def test_process_level_network_is_denied_with_eperm(self) -> None:
        network_errno = None
        client = None
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(0.1)
            client.connect(("127.0.0.1", 9))
        except OSError as exc:
            network_errno = exc.errno
        finally:
            if client is not None:
                client.close()
        self.assertEqual(network_errno, errno.EPERM)

    def test_payload_write_is_blocked_before_file_creation(self) -> None:
        target = "/tmp/tars-phase1a-forbidden-payload-{}.bin".format(os.getpid())
        with self.assertRaises(GuardViolation):
            with open(target, "wb") as payload_file:
                payload_file.write(b"forbidden")
        self.assertFalse(os.path.exists(target))

    def test_subprocess_escape_is_blocked(self) -> None:
        with self.assertRaises(GuardViolation):
            subprocess.run(["/usr/bin/true"], check=True)

    def test_environment_mutation_is_blocked(self) -> None:
        with self.assertRaises(GuardViolation):
            os.environ["TARS_GATEWAY_ENDPOINT"] = "https://forbidden.invalid"
        self.assertNotIn("TARS_GATEWAY_ENDPOINT", os.environ)


class FixtureAndSchemaGuardTests(unittest.TestCase):
    def test_manifest_is_closed_and_all_digests_verify(self) -> None:
        catalog = load_committed_catalog()
        self.assertEqual(
            catalog.fixture_ids(),
            ("counter-3200-v1", "lcg-3200-v1", "zero-3200-v1"),
        )
        for fixture_id in catalog.fixture_ids():
            with self.subTest(fixture_id=fixture_id):
                self.assertEqual(len(catalog.generate(fixture_id)), 3200)

    def test_unlisted_fixture_is_rejected(self) -> None:
        catalog = load_committed_catalog()
        with self.assertRaises(GuardViolation):
            catalog.generate("candidate-interview.wav")

    def test_committed_manifest_digest_is_pinned(self) -> None:
        self.assertEqual(
            COMMITTED_MANIFEST_SHA256,
            "69b2405ce25db344fd6178fce23c0dc7c563efb2849e0e1ab295c7fc78eb8508",
        )
        self.assertFalse(hasattr(FixtureCatalog, "from_path"))

    def test_tampered_fixture_digest_is_rejected(self) -> None:
        with MANIFEST_PATH.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
        tampered = deepcopy(manifest)
        tampered["fixtures"][0]["sha256"] = "0" * 64
        catalog = FixtureCatalog(tampered)
        with self.assertRaises(GuardViolation):
            catalog.generate("zero-3200-v1")

    def test_schema_declares_canonical_bounds_without_content_fields(self) -> None:
        with SCHEMA_PATH.open("r", encoding="utf-8") as schema_file:
            schema = json.load(schema_file)
        definitions = schema["$defs"]
        audio_properties = definitions["audioChunk"]["allOf"][1]["properties"]
        self.assertEqual(audio_properties["payloadBytes"]["maximum"], 64000)
        self.assertEqual(audio_properties["durationMs"]["maximum"], 1000)
        self.assertNotIn("audio", audio_properties)
        terminal_properties = definitions["terminalOutcome"]["allOf"][1]["properties"]
        self.assertNotIn("transcriptText", terminal_properties)
        terminal_constraints = definitions["terminalOutcome"]["allOf"][1]["allOf"]
        self.assertEqual(len(terminal_constraints), 2)


if __name__ == "__main__":
    unittest.main()

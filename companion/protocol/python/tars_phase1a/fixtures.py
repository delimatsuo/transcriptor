"""Deterministic in-memory fixture generation for Phase 1A."""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping

from .guard import GuardViolation


COMMITTED_MANIFEST_SHA256 = (
    "69b2405ce25db344fd6178fce23c0dc7c563efb2849e0e1ab295c7fc78eb8508"
)
_COMMITTED_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "phase1a-v1.manifest.json"
)


class FixtureCatalog:
    """Closed fixture catalog backed by the committed manifest."""

    def __init__(self, manifest: Mapping[str, Any]) -> None:
        if manifest.get("manifestVersion") != "phase1a-bytes-v1":
            raise GuardViolation("unsupported fixture manifest version")
        if manifest.get("contentClass") != "opaque-nonspeech":
            raise GuardViolation("fixture manifest must be opaque nonspeech")
        if manifest.get("persistence") != "memory-only":
            raise GuardViolation("fixture manifest must be memory-only")

        fixtures = manifest.get("fixtures")
        if not isinstance(fixtures, list) or not fixtures:
            raise GuardViolation("fixture manifest is empty")

        indexed: Dict[str, Mapping[str, Any]] = {}
        for fixture in fixtures:
            if not isinstance(fixture, dict):
                raise GuardViolation("fixture entry must be an object")
            fixture_id = fixture.get("id")
            if not isinstance(fixture_id, str) or fixture_id in indexed:
                raise GuardViolation("fixture IDs must be unique strings")
            indexed[fixture_id] = fixture
        self._fixtures = indexed

    def fixture_ids(self):  # type: ignore[no-untyped-def]
        return tuple(sorted(self._fixtures))

    def generate(self, fixture_id: str) -> bytes:
        try:
            fixture = self._fixtures[fixture_id]
        except KeyError as exc:
            raise GuardViolation("unlisted fixture: " + fixture_id) from exc

        length = fixture.get("lengthBytes")
        generator = fixture.get("generator")
        expected_digest = fixture.get("sha256")
        if not isinstance(length, int) or length <= 0:
            raise GuardViolation("invalid fixture length: " + fixture_id)
        if not isinstance(generator, dict):
            raise GuardViolation("invalid fixture generator: " + fixture_id)
        if not isinstance(expected_digest, str):
            raise GuardViolation("invalid fixture digest: " + fixture_id)

        payload = self._generate_bytes(length, generator)
        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != length or digest != expected_digest:
            raise GuardViolation("fixture verification failed: " + fixture_id)
        return payload

    @staticmethod
    def _generate_bytes(length: int, generator: Mapping[str, Any]) -> bytes:
        kind = generator.get("kind")
        if kind == "zero":
            return bytes(length)
        if kind == "counter-mod-256":
            return bytes(index % 256 for index in range(length))
        if kind == "lcg-31":
            state = generator.get("seed")
            multiplier = generator.get("multiplier")
            increment = generator.get("increment")
            if not all(isinstance(value, int) for value in (state, multiplier, increment)):
                raise GuardViolation("invalid LCG parameters")
            output = bytearray()
            for _ in range(length):
                state = (multiplier * state + increment) & 0x7FFFFFFF
                output.append(state & 0xFF)
            return bytes(output)
        raise GuardViolation("unsupported fixture generator: " + str(kind))


def load_committed_catalog() -> FixtureCatalog:
    """Load only the pinned, repository-owned Phase 1A manifest."""

    manifest_bytes = _COMMITTED_MANIFEST_PATH.read_bytes()
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    if digest != COMMITTED_MANIFEST_SHA256:
        raise GuardViolation("committed fixture manifest digest mismatch")

    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise GuardViolation("fixture manifest root must be an object")
    return FixtureCatalog(manifest)

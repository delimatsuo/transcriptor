"""Hardware-free tests for the deterministic physical audio gate."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from backend.config import Settings
from backend.scripts import physical_audio_gate


def _samples(frame_values: list[float], sample_rate: int = 1000) -> np.ndarray:
    return np.concatenate(
        [np.full(100, value, dtype=np.float32) for value in frame_values]
    )


def test_signal_gate_rejects_one_peak_without_sustained_audio() -> None:
    baseline = _samples([0.001] * 15)
    samples = _samples([0.0] * 50 + [0.8] + [0.0] * 199)

    metrics = physical_audio_gate.analyze_signal(samples, baseline, 1000)

    assert metrics.peak == np.float32(0.8)
    assert metrics.first_active_seconds == 5.0
    assert metrics.active_seconds == 0.1
    assert metrics.sustained_signal is False


def test_signal_gate_accepts_sustained_speech_energy() -> None:
    baseline = _samples([0.001] * 15)
    samples = _samples([0.002] * 20 + [0.06] * 20 + [0.002] * 20)

    metrics = physical_audio_gate.analyze_signal(samples, baseline, 1000)

    assert metrics.active_seconds == 2.0
    assert metrics.first_active_seconds == 2.0
    assert metrics.longest_active_seconds == 2.0
    assert metrics.signal_to_noise_db > 6
    assert metrics.sustained_signal is True


def test_microphone_capture_starts_only_after_audible_cue(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    streams: list[object] = []

    class FakeInputStream:
        def __init__(self, *, channels: int, callback, **_: object) -> None:
            self.channels = channels
            self.callback = callback
            streams.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def fake_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))
        value = 0.001 if seconds == physical_audio_gate.BASELINE_SECONDS else 0.06
        for stream in streams:
            data = np.full((100, stream.channels), value, dtype=np.float32)
            stream.callback(data, 100, {}, None)

    def fake_say(message: str) -> None:
        events.append(("say", message))

    monkeypatch.setattr(physical_audio_gate.sd, "InputStream", FakeInputStream)
    settings = Settings(
        google_cloud_project="test-project",
        sample_rate=1000,
        microphone_input_channel=4,
    )

    _, samples = physical_audio_gate.capture_phase(
        "microphone",
        settings,
        devices=((3, "Vocaster One USB"), (4, "BlackHole 2ch")),
        say=fake_say,
        sleep=fake_sleep,
    )

    assert events[0] == ("sleep", physical_audio_gate.BASELINE_SECONDS)
    assert events[1] == ("say", physical_audio_gate.MICROPHONE_CUE)
    assert events[2] == ("sleep", physical_audio_gate.MICROPHONE_CAPTURE_SECONDS)
    assert events[3][0] == "say"
    assert np.all(samples["microphoneBaseline"] == np.float32(0.001))
    assert np.all(samples["microphone"] == np.float32(0.06))


def test_gate_configuration_rejects_mutable_environment_drift() -> None:
    settings = Settings(
        google_cloud_project=physical_audio_gate.EXPECTED_PROJECT,
        microphone_input_channel=physical_audio_gate.EXPECTED_MICROPHONE_CHANNEL,
    )
    physical_audio_gate.validate_gate_configuration(
        settings,
        "Vocaster One USB",
        "BlackHole 2ch",
    )

    wrong_channel = settings.model_copy(update={"microphone_input_channel": 0})
    try:
        physical_audio_gate.validate_gate_configuration(
            wrong_channel,
            "Vocaster One USB",
            "BlackHole 2ch",
        )
    except RuntimeError as exc:
        assert "microphoneInputChannel" in str(exc)
    else:
        raise AssertionError("gate accepted the wrong microphone channel")


def test_output_route_is_fixed_by_phase() -> None:
    assert (
        physical_audio_gate.validate_output_route("microphone", "Deli's AirPods Max")
        == "AirPods"
    )
    assert (
        physical_audio_gate.validate_output_route(
            "system-audio", "Transcriptor Output"
        )
        == "Transcriptor Output"
    )
    try:
        physical_audio_gate.validate_output_route("microphone", "MacBook Speakers")
    except RuntimeError as exc:
        assert "expected 'AirPods'" in str(exc)
    else:
        raise AssertionError("microphone phase accepted the wrong output route")


def test_capture_integrity_rejects_callback_faults_and_truncation() -> None:
    complete = {
        "microphone": np.zeros(16_000, dtype=np.float32),
        "systemAudio": np.zeros(16_000, dtype=np.float32),
        "microphoneBaseline": np.zeros(24_000, dtype=np.float32),
        "systemAudioBaseline": np.zeros(24_000, dtype=np.float32),
    }
    metadata = {"callbackStatusOk": True, "captureDurationSeconds": 1.0}

    assert physical_audio_gate.capture_integrity_ok(metadata, complete, 16_000)
    assert not physical_audio_gate.capture_integrity_ok(
        {**metadata, "callbackStatusOk": False}, complete, 16_000
    )
    assert not physical_audio_gate.capture_integrity_ok(
        metadata,
        {**complete, "systemAudio": np.zeros(100, dtype=np.float32)},
        16_000,
    )
    assert not physical_audio_gate.capture_integrity_ok(
        metadata,
        {**complete, "microphoneBaseline": np.zeros(100, dtype=np.float32)},
        16_000,
    )


def _signal(*, sustained: bool) -> physical_audio_gate.SignalMetrics:
    return physical_audio_gate.SignalMetrics(
        peak=0.2 if sustained else 0.0,
        rms=0.05 if sustained else 0.0,
        noise_rms=0.001,
        threshold=0.008,
        p95_frame_rms=0.06 if sustained else 0.0,
        first_active_seconds=0.2 if sustained else None,
        active_seconds=2.0 if sustained else 0.0,
        longest_active_seconds=1.0 if sustained else 0.0,
        signal_to_noise_db=20.0 if sustained else -80.0,
        sustained_signal=sustained,
    )


def _provider(*, final_characters: int) -> physical_audio_gate.ProviderMetrics:
    return physical_audio_gate.ProviderMetrics(
        request_opened=True,
        final_character_count=final_characters,
        drain_completed=True,
    )


def test_pass_composition_rejects_local_isolation_leakage() -> None:
    active_provider = _provider(final_characters=30)
    isolation_provider = _provider(final_characters=0)

    assert physical_audio_gate.gate_passes(
        integrity_ok=True,
        active_signal=_signal(sustained=True),
        isolation_signal=_signal(sustained=False),
        active_provider=active_provider,
        isolation_provider=isolation_provider,
    )
    assert not physical_audio_gate.gate_passes(
        integrity_ok=True,
        active_signal=_signal(sustained=True),
        isolation_signal=_signal(sustained=True),
        active_provider=active_provider,
        isolation_provider=isolation_provider,
    )
    assert not physical_audio_gate.gate_passes(
        integrity_ok=False,
        active_signal=_signal(sustained=True),
        isolation_signal=_signal(sustained=False),
        active_provider=active_provider,
        isolation_provider=isolation_provider,
    )


def test_artifact_binding_rejects_an_untracked_harness(monkeypatch, tmp_path) -> None:
    repository = tmp_path / "repo"
    harness = repository / "backend/scripts/physical_audio_gate.py"

    def fake_run(command: list[str], **_: object):
        if command == ["git", "rev-parse", "--show-toplevel"]:
            return SimpleNamespace(stdout=f"{repository}\n", returncode=0)
        if command == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(stdout=f"{'a' * 40}\n", returncode=0)
        if command[:3] == ["git", "ls-files", "--error-unmatch"]:
            return SimpleNamespace(stdout="", returncode=1)
        raise AssertionError(f"unexpected Git command: {command}")

    monkeypatch.setattr(physical_audio_gate.subprocess, "run", fake_run)

    try:
        physical_audio_gate._git_artifact("a" * 40, harness_path=Path(harness))
    except RuntimeError as exc:
        assert "not tracked" in str(exc)
    else:
        raise AssertionError("gate accepted an untracked harness")


def test_artifact_binding_rejects_a_dirty_tracked_worktree(monkeypatch, tmp_path) -> None:
    repository = tmp_path / "repo"
    harness = repository / "backend/scripts/physical_audio_gate.py"
    blob = "b" * 40

    def fake_run(command: list[str], **_: object):
        if command == ["git", "rev-parse", "--show-toplevel"]:
            return SimpleNamespace(stdout=f"{repository}\n", returncode=0)
        if command == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(stdout=f"{'a' * 40}\n", returncode=0)
        if command[:3] == ["git", "ls-files", "--error-unmatch"]:
            return SimpleNamespace(stdout=str(harness), returncode=0)
        if command[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(stdout=f"{blob}\n", returncode=0)
        if command[:2] == ["git", "hash-object"]:
            return SimpleNamespace(stdout=f"{blob}\n", returncode=0)
        if command == ["git", "diff", "--quiet"]:
            return SimpleNamespace(stdout="", returncode=1)
        if command == ["git", "diff", "--cached", "--quiet"]:
            return SimpleNamespace(stdout="", returncode=0)
        raise AssertionError(f"unexpected Git command: {command}")

    monkeypatch.setattr(physical_audio_gate.subprocess, "run", fake_run)

    try:
        physical_audio_gate._git_artifact("a" * 40, harness_path=Path(harness))
    except RuntimeError as exc:
        assert "not clean" in str(exc)
    else:
        raise AssertionError("gate accepted a dirty worktree")


class _FinalAfterCloseStream:
    instances: list["_FinalAfterCloseStream"] = []

    def __init__(self, settings: Settings, stream_id: str) -> None:
        self.settings = settings
        self.stream_id = stream_id
        self.request_opened = False
        self._active = False
        self._closed = asyncio.Event()
        self.audio: list[bytes] = []
        self.__class__.instances.append(self)

    @property
    def is_active(self) -> bool:
        return self._active

    async def start(self):
        self._active = True
        self.request_opened = True
        await self._closed.wait()
        alternative = SimpleNamespace(transcript="conteúdo privado nunca impresso")
        result = SimpleNamespace(alternatives=[alternative], is_final=True)
        yield SimpleNamespace(results=[result], speech_event_type=1)
        self._active = False

    async def send_audio(self, audio_bytes: bytes) -> None:
        assert self._active
        self.audio.append(audio_bytes)

    async def stop(self) -> None:
        self._active = False
        self._closed.set()


def test_provider_session_half_closes_and_counts_content_free_metadata() -> None:
    _FinalAfterCloseStream.instances.clear()
    settings = Settings(
        google_cloud_project="test-project",
        sample_rate=1000,
        audio_chunk_duration_ms=100,
    )

    metrics = asyncio.run(
        physical_audio_gate.transcribe_memory_audio(
            np.full(250, 0.05, dtype=np.float32),
            settings,
            "gate-test",
            stream_factory=_FinalAfterCloseStream,
            realtime=False,
        )
    )

    assert len(_FinalAfterCloseStream.instances) == 1
    assert metrics.request_opened is True
    assert metrics.drain_completed is True
    assert metrics.frames_sent == 250
    assert metrics.bytes_sent == 500
    assert metrics.voice_activity_event_count == 1
    assert metrics.final_result_count == 1
    assert metrics.final_character_count == len("conteúdo privado nunca impresso")
    assert not hasattr(metrics, "transcript")


def test_provider_audio_requires_explicit_acknowledgement() -> None:
    args = SimpleNamespace(
        phase="microphone",
        expected_sha="a" * 40,
        send_to_provider=True,
        confirm_provider_audio=False,
    )

    try:
        asyncio.run(physical_audio_gate._run(args))
    except RuntimeError as exc:
        assert "explicit --confirm-provider-audio" in str(exc)
    else:  # pragma: no cover - guards against a future fail-open refactor
        raise AssertionError("provider audio ran without acknowledgement")

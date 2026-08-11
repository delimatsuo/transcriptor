"""Deterministic, privacy-safe macOS physical audio isolation gate.

This is a release-evidence harness, not application runtime.  It replaces
chat-timed prompts with an in-process audible cue and runs exactly one source
phase per process so late provider finals cannot cross a phase boundary.

Examples (run twice per phase from a clean exact-commit worktree):

  python -m backend.scripts.physical_audio_gate \
    --phase microphone --expected-sha <sha> \
    --send-to-provider --confirm-provider-audio

  python -m backend.scripts.physical_audio_gate \
    --phase system-audio --expected-sha <sha> \
    --send-to-provider --confirm-provider-audio

Only aggregate metadata is printed.  Captured audio and transcript text stay
in memory and are discarded when the process exits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import subprocess
import threading
import time
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd

from backend.audio.capture import find_input_device, get_default_input_device
from backend.config import Settings, get_settings
from backend.startup_credentials import probe_application_default_credentials
from backend.stt.google_stt import GoogleSTTStream

FRAME_SECONDS = 0.1
BASELINE_SECONDS = 1.5
MICROPHONE_CAPTURE_SECONDS = 25.0
MIN_ACTIVE_SECONDS = 1.5
MIN_CONSECUTIVE_ACTIVE_SECONDS = 0.3
MIN_SIGNAL_TO_NOISE_DB = 6.0
MIN_FINAL_CHARACTERS = 20
MIN_RMS_THRESHOLD = 0.008
CAPTURE_DURATION_TOLERANCE_RATIO = 0.10
CAPTURE_DURATION_TOLERANCE_SECONDS = 0.5

EXPECTED_MICROPHONE_DEVICE = "Vocaster One USB"
EXPECTED_MICROPHONE_CHANNEL = 4
EXPECTED_SYSTEM_DEVICE = "BlackHole 2ch"
EXPECTED_PROJECT = "transcriptor-490222"
EXPECTED_SAMPLE_RATE = 16000
EXPECTED_CHANNELS = 1
EXPECTED_LANGUAGE = "pt-BR"
EXPECTED_MODEL = "chirp_3"
EXPECTED_LOCATION = "us"
EXPECTED_OUTPUT_BY_PHASE = {
    "microphone": "AirPods",
    "system-audio": "Transcriptor Output",
}
HARNESS_PATH = Path(__file__).resolve()

MICROPHONE_CUE = (
    "Teste do microfone. Quando ouvir fale agora, leia qualquer texto em "
    "português até eu avisar que terminou. Fale agora."
)
SYSTEM_TEST_SPEECH = (
    "Este é um teste automático do áudio do sistema. A transcrição deve "
    "aparecer somente no canal do candidato. O microfone deve permanecer "
    "isolado durante toda esta mensagem. Vamos repetir para confirmar a "
    "estabilidade e a separação correta dos canais. "
)


@dataclass(frozen=True)
class SignalMetrics:
    """Content-free signal measurements used before any provider request."""

    peak: float
    rms: float
    noise_rms: float
    threshold: float
    p95_frame_rms: float
    first_active_seconds: float | None
    active_seconds: float
    longest_active_seconds: float
    signal_to_noise_db: float
    sustained_signal: bool


@dataclass
class ProviderMetrics:
    """Provider evidence that deliberately excludes transcript content."""

    request_opened: bool = False
    response_count: int = 0
    voice_activity_event_count: int = 0
    interim_result_count: int = 0
    final_result_count: int = 0
    final_character_count: int = 0
    frames_sent: int = 0
    bytes_sent: int = 0
    drain_completed: bool = False


class _ChannelCollector:
    """Thread-safe PortAudio callback collector with explicit capture states."""

    def __init__(self, input_channel: int) -> None:
        self.input_channel = input_channel
        self.baseline_chunks: list[np.ndarray] = []
        self.capture_chunks: list[np.ndarray] = []
        self.status_count = 0
        self._state = "ignore"
        self._lock = threading.Lock()

    def set_state(self, state: str) -> None:
        if state not in {"ignore", "baseline", "capture"}:
            raise ValueError(f"unsupported collector state: {state}")
        with self._lock:
            self._state = state

    def callback(
        self,
        indata: np.ndarray,
        _frames: int,
        _time_info: object,
        status: object,
    ) -> None:
        if status:
            self.status_count += 1
        if indata.ndim > 1:
            if self.input_channel >= indata.shape[1]:
                self.status_count += 1
                return
            selected = indata[:, self.input_channel]
        else:
            selected = indata
        chunk = np.asarray(selected, dtype=np.float32).copy()
        with self._lock:
            if self._state == "baseline":
                self.baseline_chunks.append(chunk)
            elif self._state == "capture":
                self.capture_chunks.append(chunk)

    @staticmethod
    def _join(chunks: list[np.ndarray]) -> np.ndarray:
        if not chunks:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(chunks)

    @property
    def baseline(self) -> np.ndarray:
        return self._join(self.baseline_chunks)

    @property
    def capture(self) -> np.ndarray:
        return self._join(self.capture_chunks)


def _frame_rms(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    frame_size = max(1, int(sample_rate * FRAME_SECONDS))
    frame_count = len(samples) // frame_size
    if frame_count == 0:
        return np.empty(0, dtype=np.float64)
    framed = samples[: frame_count * frame_size].reshape(frame_count, frame_size)
    return np.sqrt(np.mean(np.square(framed.astype(np.float64)), axis=1))


def analyze_signal(
    samples: np.ndarray,
    baseline: np.ndarray,
    sample_rate: int,
) -> SignalMetrics:
    """Require sustained speech-shaped energy, not one noisy peak."""
    frames = _frame_rms(samples, sample_rate)
    baseline_frames = _frame_rms(baseline, sample_rate)
    noise_rms = (
        float(np.percentile(baseline_frames, 95))
        if baseline_frames.size
        else 0.0
    )
    threshold = max(MIN_RMS_THRESHOLD, noise_rms * 3.0)
    active = frames >= threshold

    longest = 0
    current = 0
    for is_active in active:
        if is_active:
            current += 1
            longest = max(longest, current)
        else:
            current = 0

    active_seconds = float(np.count_nonzero(active)) * FRAME_SECONDS
    active_indexes = np.flatnonzero(active)
    first_active_seconds = (
        float(active_indexes[0]) * FRAME_SECONDS if active_indexes.size else None
    )
    longest_active_seconds = longest * FRAME_SECONDS
    overall_rms = (
        float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
        if samples.size
        else 0.0
    )
    active_rms = (
        float(np.sqrt(np.mean(np.square(frames[active]))))
        if np.any(active)
        else 0.0
    )
    denominator = max(noise_rms, 1e-5)
    signal_to_noise_db = 20.0 * math.log10(max(active_rms, 1e-9) / denominator)
    p95 = float(np.percentile(frames, 95)) if frames.size else 0.0
    sustained = (
        active_seconds >= MIN_ACTIVE_SECONDS
        and longest_active_seconds >= MIN_CONSECUTIVE_ACTIVE_SECONDS
        and p95 >= threshold * 1.5
        and signal_to_noise_db >= MIN_SIGNAL_TO_NOISE_DB
    )

    return SignalMetrics(
        peak=float(np.max(np.abs(samples))) if samples.size else 0.0,
        rms=overall_rms,
        noise_rms=noise_rms,
        threshold=threshold,
        p95_frame_rms=p95,
        first_active_seconds=first_active_seconds,
        active_seconds=active_seconds,
        longest_active_seconds=longest_active_seconds,
        signal_to_noise_db=signal_to_noise_db,
        sustained_signal=sustained,
    )


def _run_say(message: str) -> None:
    subprocess.run(["say", message], check=True)


def _resolve_default_output() -> tuple[int, str]:
    output_index = sd.default.device[1]
    if output_index is None or output_index < 0:
        raise RuntimeError("No default output device found")
    output = sd.query_devices(output_index)
    return int(output_index), str(output["name"])


def validate_output_route(phase: str, output_name: str) -> str:
    """Bind each phase to its approved physical output route."""
    expected_output = EXPECTED_OUTPUT_BY_PHASE[phase]
    if expected_output.lower() not in output_name.lower():
        raise RuntimeError(
            f"output route mismatch for {phase}: expected {expected_output!r}, "
            f"found {output_name!r}"
        )
    return expected_output


def _resolve_devices(settings: Settings) -> tuple[tuple[int, str], tuple[int, str]]:
    microphone_index = (
        find_input_device(settings.microphone_device_name)
        if settings.microphone_device_name
        else get_default_input_device()
    )
    system_index = find_input_device(settings.blackhole_device_name)
    microphone_name = str(sd.query_devices(microphone_index)["name"])
    system_name = str(sd.query_devices(system_index)["name"])
    return (microphone_index, microphone_name), (system_index, system_name)


def validate_gate_configuration(
    settings: Settings,
    microphone_name: str,
    system_name: str,
) -> dict[str, object]:
    """Fail closed unless this is the owner-approved Week 4 Mac target."""
    actual = {
        "microphoneDeviceName": microphone_name,
        "microphoneInputChannel": settings.microphone_input_channel,
        "systemAudioDeviceName": system_name,
        "providerProject": settings.google_cloud_project,
        "sampleRate": settings.sample_rate,
        "channels": settings.channels,
        "language": settings.stt_language_code,
        "model": settings.stt_model,
        "location": settings.stt_location,
    }
    expected = {
        "microphoneDeviceName": EXPECTED_MICROPHONE_DEVICE,
        "microphoneInputChannel": EXPECTED_MICROPHONE_CHANNEL,
        "systemAudioDeviceName": EXPECTED_SYSTEM_DEVICE,
        "providerProject": EXPECTED_PROJECT,
        "sampleRate": EXPECTED_SAMPLE_RATE,
        "channels": EXPECTED_CHANNELS,
        "language": EXPECTED_LANGUAGE,
        "model": EXPECTED_MODEL,
        "location": EXPECTED_LOCATION,
    }
    mismatches = [
        key
        for key, expected_value in expected.items()
        if (
            expected_value.lower() not in str(actual[key]).lower()
            if key in {"microphoneDeviceName", "systemAudioDeviceName"}
            else actual[key] != expected_value
        )
    ]
    if mismatches:
        details = ", ".join(
            f"{key}: expected {expected[key]!r}, found {actual[key]!r}"
            for key in mismatches
        )
        raise RuntimeError(f"physical gate configuration mismatch: {details}")
    return actual


def capture_phase(
    phase: str,
    settings: Settings,
    *,
    devices: tuple[tuple[int, str], tuple[int, str]] | None = None,
    say: Callable[[str], None] = _run_say,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, object], dict[str, np.ndarray]]:
    """Capture both physical sources with an in-process phase trigger."""
    microphone, system = devices or _resolve_devices(settings)
    microphone_collector = _ChannelCollector(settings.microphone_input_channel)
    system_collector = _ChannelCollector(0)
    started_at = time.monotonic()

    with ExitStack() as stack:
        stack.enter_context(
            sd.InputStream(
                device=microphone[0],
                channels=max(1, settings.microphone_input_channel + 1),
                samplerate=settings.sample_rate,
                dtype="float32",
                callback=microphone_collector.callback,
            )
        )
        stack.enter_context(
            sd.InputStream(
                device=system[0],
                channels=1,
                samplerate=settings.sample_rate,
                dtype="float32",
                callback=system_collector.callback,
            )
        )

        microphone_collector.set_state("baseline")
        system_collector.set_state("baseline")
        sleep(BASELINE_SECONDS)
        microphone_collector.set_state("ignore")
        system_collector.set_state("ignore")

        if phase == "microphone":
            say(MICROPHONE_CUE)
            cue_completed_at = time.monotonic()
            microphone_collector.set_state("capture")
            system_collector.set_state("capture")
            sleep(MICROPHONE_CAPTURE_SECONDS)
        elif phase == "system-audio":
            cue_completed_at = time.monotonic()
            microphone_collector.set_state("capture")
            system_collector.set_state("capture")
            say(SYSTEM_TEST_SPEECH * 2)
            sleep(1.0)
        else:  # guarded by argparse; retained for direct callers/tests
            raise ValueError(f"unsupported phase: {phase}")

        microphone_collector.set_state("ignore")
        system_collector.set_state("ignore")
        capture_stopped_at = time.monotonic()

    if phase == "microphone":
        say("Teste concluído. Obrigado.")

    metadata: dict[str, object] = {
        "cueCompletedSeconds": round(cue_completed_at - started_at, 3),
        "captureStoppedSeconds": round(capture_stopped_at - started_at, 3),
        "captureDurationSeconds": round(capture_stopped_at - cue_completed_at, 3),
        "callbackStatusOk": (
            microphone_collector.status_count == 0
            and system_collector.status_count == 0
        ),
        "microphone": {
            "deviceIndex": microphone[0],
            "deviceName": microphone[1],
            "inputChannel": settings.microphone_input_channel,
            "callbackStatusCount": microphone_collector.status_count,
        },
        "systemAudio": {
            "deviceIndex": system[0],
            "deviceName": system[1],
            "inputChannel": 0,
            "callbackStatusCount": system_collector.status_count,
        },
    }
    samples = {
        "microphone": microphone_collector.capture,
        "microphoneBaseline": microphone_collector.baseline,
        "systemAudio": system_collector.capture,
        "systemAudioBaseline": system_collector.baseline,
    }
    return metadata, samples


def capture_integrity_ok(
    capture_metadata: dict[str, object],
    samples: dict[str, np.ndarray],
    sample_rate: int,
) -> bool:
    """Reject callback faults and capture lengths inconsistent with wall time."""
    if not bool(capture_metadata.get("callbackStatusOk")):
        return False
    capture_seconds = float(capture_metadata.get("captureDurationSeconds", 0.0))
    if capture_seconds <= 0:
        return False
    expected_samples = capture_seconds * sample_rate
    tolerance = max(
        CAPTURE_DURATION_TOLERANCE_SECONDS * sample_rate,
        expected_samples * CAPTURE_DURATION_TOLERANCE_RATIO,
    )
    capture_lengths_ok = all(
        abs(len(samples[source]) - expected_samples) <= tolerance
        for source in ("microphone", "systemAudio")
    )
    expected_baseline_samples = BASELINE_SECONDS * sample_rate
    baseline_tolerance = max(
        CAPTURE_DURATION_TOLERANCE_SECONDS * sample_rate,
        expected_baseline_samples * CAPTURE_DURATION_TOLERANCE_RATIO,
    )
    baseline_lengths_ok = all(
        abs(len(samples[source]) - expected_baseline_samples) <= baseline_tolerance
        for source in ("microphoneBaseline", "systemAudioBaseline")
    )
    return capture_lengths_ok and baseline_lengths_ok


def gate_passes(
    *,
    integrity_ok: bool,
    active_signal: SignalMetrics,
    isolation_signal: SignalMetrics,
    active_provider: ProviderMetrics | None,
    isolation_provider: ProviderMetrics | None,
) -> bool:
    """Compose every independent release-gate condition in one predicate."""
    return bool(
        integrity_ok
        and active_signal.sustained_signal
        and not isolation_signal.sustained_signal
        and active_provider is not None
        and active_provider.request_opened
        and active_provider.drain_completed
        and active_provider.final_character_count >= MIN_FINAL_CHARACTERS
        and isolation_provider is not None
        and isolation_provider.request_opened
        and isolation_provider.drain_completed
        and isolation_provider.final_character_count == 0
    )


async def transcribe_memory_audio(
    samples: np.ndarray,
    settings: Settings,
    stream_id: str,
    *,
    stream_factory: type[GoogleSTTStream] = GoogleSTTStream,
    realtime: bool = True,
) -> ProviderMetrics:
    """Send one in-memory source through one fully drained provider stream."""
    metrics = ProviderMetrics()
    stream = stream_factory(settings=settings, stream_id=stream_id)

    async def consume() -> None:
        async for response in stream.start():
            metrics.response_count += 1
            if getattr(response, "speech_event_type", 0):
                metrics.voice_activity_event_count += 1
            for result in getattr(response, "results", ()):
                alternatives = getattr(result, "alternatives", ())
                if not alternatives:
                    continue
                transcript = str(getattr(alternatives[0], "transcript", "")).strip()
                if not transcript:
                    continue
                if bool(getattr(result, "is_final", False)):
                    metrics.final_result_count += 1
                    metrics.final_character_count += len(transcript)
                else:
                    metrics.interim_result_count += 1

    consumer = asyncio.create_task(consume())
    stop_requested = False
    try:
        for _ in range(200):
            if stream.is_active:
                break
            if consumer.done():
                await consumer
                break
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("provider stream did not become active")

        chunk_size = settings.chunk_size
        for offset in range(0, len(samples), chunk_size):
            chunk = np.clip(samples[offset : offset + chunk_size], -1.0, 1.0)
            audio_bytes = (chunk * 32767).astype(np.int16).tobytes()
            await stream.send_audio(audio_bytes)
            metrics.frames_sent += len(chunk)
            metrics.bytes_sent += len(audio_bytes)
            if realtime:
                await asyncio.sleep(len(chunk) / settings.sample_rate)

        await stream.stop()
        stop_requested = True
        await asyncio.wait_for(
            consumer,
            timeout=settings.stt_graceful_drain_timeout_seconds + 2.0,
        )
        metrics.drain_completed = True
    finally:
        if not stop_requested:
            await stream.stop()
        if not consumer.done():
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        metrics.request_opened = bool(getattr(stream, "request_opened", False))

    return metrics


def _git_artifact(
    expected_sha: str,
    *,
    harness_path: Path = HARNESS_PATH,
) -> dict[str, object]:
    repository_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    ).resolve()
    try:
        relative_harness = harness_path.resolve().relative_to(repository_root)
    except ValueError:
        raise RuntimeError("executed harness is outside the Git worktree") from None
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(relative_harness)],
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0
    if not tracked:
        raise RuntimeError("executed physical audio harness is not tracked by Git")
    head_blob = subprocess.run(
        ["git", "rev-parse", f"HEAD:{relative_harness}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    worktree_blob = subprocess.run(
        ["git", "hash-object", str(relative_harness)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head_blob != worktree_blob:
        raise RuntimeError("executed physical audio harness differs from HEAD")
    worktree_clean = subprocess.run(
        ["git", "diff", "--quiet"], check=False
    ).returncode == 0
    index_clean = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], check=False
    ).returncode == 0
    if head != expected_sha:
        raise RuntimeError(f"exact SHA mismatch: expected {expected_sha}, found {head}")
    if not worktree_clean or not index_clean:
        raise RuntimeError("tracked worktree or Git index is not clean")
    return {
        "headSha": head,
        "trackedWorktreeClean": worktree_clean,
        "indexClean": index_clean,
        "harnessPath": str(relative_harness),
        "harnessBlob": head_blob,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("microphone", "system-audio"), required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--send-to-provider", action="store_true")
    parser.add_argument("--confirm-provider-audio", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> tuple[dict[str, object], bool]:
    if platform.system() != "Darwin":
        raise RuntimeError("physical_audio_gate currently supports macOS only")
    if args.confirm_provider_audio and not args.send_to_provider:
        raise RuntimeError("--confirm-provider-audio requires --send-to-provider")
    if args.send_to_provider and not args.confirm_provider_audio:
        raise RuntimeError(
            "provider audio requires explicit --confirm-provider-audio acknowledgement"
        )

    artifact = _git_artifact(args.expected_sha)
    settings = get_settings()
    devices = _resolve_devices(settings)
    gate_configuration = validate_gate_configuration(
        settings, devices[0][1], devices[1][1]
    )
    output_index, output_name = _resolve_default_output()
    expected_output_name = validate_output_route(args.phase, output_name)
    gate_configuration["expectedOutputDeviceName"] = expected_output_name
    gate_configuration["resolvedOutputDeviceName"] = output_name

    if args.send_to_provider:
        await probe_application_default_credentials()

    capture_metadata, samples = capture_phase(args.phase, settings, devices=devices)
    microphone_metrics = analyze_signal(
        samples["microphone"],
        samples["microphoneBaseline"],
        settings.sample_rate,
    )
    system_metrics = analyze_signal(
        samples["systemAudio"],
        samples["systemAudioBaseline"],
        settings.sample_rate,
    )
    local_metrics = {
        "microphone": asdict(microphone_metrics),
        "systemAudio": asdict(system_metrics),
    }
    active_name = "microphone" if args.phase == "microphone" else "systemAudio"
    isolation_name = "systemAudio" if args.phase == "microphone" else "microphone"
    active_signal = microphone_metrics if active_name == "microphone" else system_metrics
    isolation_signal = system_metrics if active_name == "microphone" else microphone_metrics
    integrity_ok = capture_integrity_ok(capture_metadata, samples, settings.sample_rate)

    provider: dict[str, object] | None = None
    active_provider: ProviderMetrics | None = None
    isolation_provider: ProviderMetrics | None = None
    if (
        args.send_to_provider
        and integrity_ok
        and active_signal.sustained_signal
        and not isolation_signal.sustained_signal
    ):
        active_provider = await transcribe_memory_audio(
            samples[active_name], settings, f"gate-{args.phase}-active"
        )
        isolation_provider = await transcribe_memory_audio(
            samples[isolation_name], settings, f"gate-{args.phase}-isolation"
        )
        provider = {
            "activeSource": asdict(active_provider),
            "isolationSource": asdict(isolation_provider),
        }
    local_passed = (
        integrity_ok
        and active_signal.sustained_signal
        and not isolation_signal.sustained_signal
    )
    release_passed = gate_passes(
        integrity_ok=integrity_ok,
        active_signal=active_signal,
        isolation_signal=isolation_signal,
        active_provider=active_provider,
        isolation_provider=isolation_provider,
    )
    successful = release_passed if args.send_to_provider else local_passed
    result: dict[str, object] = {
        "status": (
            "PASS"
            if release_passed
            else "FAIL"
            if args.send_to_provider
            else "LOCAL_PASS"
            if local_passed
            else "LOCAL_FAIL"
        ),
        "phase": args.phase,
        "artifact": artifact,
        "gateConfiguration": gate_configuration,
        "providerAudioAuthorized": bool(args.send_to_provider),
        "audio": {
            "sampleRate": settings.sample_rate,
            "channelsSent": settings.channels,
            "capture": capture_metadata,
            "captureIntegrityOk": integrity_ok,
            "localSignal": local_metrics,
            "activeSource": active_name,
            "isolationSource": isolation_name,
        },
        "provider": provider,
        "outputRoute": {"deviceIndex": output_index, "deviceName": output_name},
        "privacy": {
            "rawAudioPersisted": False,
            "transcriptTextPrinted": False,
            "transcriptTextPersisted": False,
        },
    }
    return result, successful


def main() -> None:
    args = _parser().parse_args()
    result, passed = asyncio.run(_run(args))
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

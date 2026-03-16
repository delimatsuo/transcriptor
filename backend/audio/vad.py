"""Voice Activity Detection using Silero VAD."""

from __future__ import annotations

import numpy as np
import structlog
import torch

from backend.config import Settings

logger = structlog.get_logger()


class VoiceActivityDetector:
    """Silero VAD gate — filters silence before STT to save cost."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: torch.jit.ScriptModule | None = None
        self._threshold = settings.vad_threshold
        self._min_speech_chunks = max(
            1, settings.vad_min_speech_duration_ms // settings.audio_chunk_duration_ms
        )
        self._silence_timeout_chunks = max(
            1, settings.vad_silence_timeout_ms // settings.audio_chunk_duration_ms
        )

        # State
        self._speech_chunks = 0
        self._silence_chunks = 0
        self._in_speech = False
        self._total_chunks = 0

    def load_model(self) -> None:
        """Load the Silero VAD model."""
        model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        self._model = model
        logger.info("vad_model_loaded")

    def reset(self) -> None:
        """Reset VAD state for a new session."""
        self._speech_chunks = 0
        self._silence_chunks = 0
        self._in_speech = False
        self._total_chunks = 0
        if self._model is not None:
            self._model.reset_states()

    @property
    def _vad_window_size(self) -> int:
        """Silero VAD requires exactly 512 samples at 16kHz (or 256 at 8kHz)."""
        return 512 if self.settings.sample_rate == 16000 else 256

    def is_speech(self, chunk: np.ndarray) -> bool:
        """Determine if an audio chunk contains speech.

        Returns True if the chunk should be forwarded to STT.
        Splits large chunks into 512-sample windows for Silero VAD,
        then applies a state machine with min-speech and silence-timeout hysteresis.
        """
        if self._model is None:
            raise RuntimeError("VAD model not loaded. Call load_model() first.")

        # Convert to 1D float tensor
        tensor = torch.from_numpy(chunk).float()
        if tensor.dim() > 1:
            tensor = tensor.mean(dim=-1)

        # Process in 512-sample windows as required by Silero VAD
        window = self._vad_window_size
        max_prob = 0.0
        for start in range(0, len(tensor), window):
            segment = tensor[start : start + window]
            if len(segment) < window:
                # Pad the last short segment with zeros
                segment = torch.nn.functional.pad(segment, (0, window - len(segment)))
            prob = self._model(segment, self.settings.sample_rate).item()
            max_prob = max(max_prob, prob)

        self._total_chunks += 1
        # Log every ~2 seconds (20 chunks at 100ms) for diagnostics
        if self._total_chunks % 20 == 0:
            logger.info(
                "vad_probe",
                max_prob=round(max_prob, 3),
                in_speech=self._in_speech,
                total_chunks=self._total_chunks,
                audio_rms=round(float(tensor.abs().mean()), 6),
            )

        if max_prob >= self._threshold:
            self._speech_chunks += 1
            self._silence_chunks = 0

            if not self._in_speech and self._speech_chunks >= self._min_speech_chunks:
                self._in_speech = True
                logger.debug("vad_speech_start", probability=round(max_prob, 3))
        else:
            self._silence_chunks += 1

            if self._in_speech and self._silence_chunks >= self._silence_timeout_chunks:
                self._in_speech = False
                self._speech_chunks = 0
                logger.debug("vad_speech_end", silence_chunks=self._silence_chunks)

        return self._in_speech

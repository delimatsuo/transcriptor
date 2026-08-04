"""STT Stream Lifecycle Manager — handles stream rotation and result stitching."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import AsyncIterator, Callable

import structlog
from google.cloud.speech_v2.types import cloud_speech

from backend.config import Settings
from backend.schemas.models import TranscriptSegment
from backend.stt.google_stt import GoogleSTTStream

logger = structlog.get_logger()


class StreamManager:
    """Manages Google STT stream rotation to work around the 5-minute limit.

    Lifecycle:
    1. Open stream A
    2. At ~4:30, open stream B (overlap period)
    3. Close stream A after receiving its final results
    4. Stitch results using timestamps to merge overlapping segments
    5. Repeat
    """

    def __init__(
        self,
        settings: Settings,
        on_transcript: Callable[[TranscriptSegment], None] | None = None,
        source_label: str = "",
    ) -> None:
        self.settings = settings
        self.on_transcript = on_transcript
        self.source_label = source_label

        self._current_stream: GoogleSTTStream | None = None
        self._stream_counter = 0
        self._sequence_number = 0
        self._session_start_time: float = 0.0
        self._running = False

        # Track last emitted end_time to avoid duplicate segments during overlap
        self._last_emitted_end_time: float = 0.0

        # Audio arriving while no stream is active (rotation/recovery window)
        # is buffered here and flushed, in order, on the next active send.
        self._pending_audio: deque[bytes] = deque(
            maxlen=settings.buffer_max_chunks
        )

        # Tasks
        self._response_task: asyncio.Task | None = None
        self._rotation_task: asyncio.Task | None = None

    def _next_stream_id(self) -> str:
        self._stream_counter += 1
        return f"stream-{self._stream_counter}"

    async def start(self) -> None:
        """Start the stream manager with auto-recovering response loop."""
        self._session_start_time = time.monotonic()
        self._running = True
        self._sequence_number = 0
        self._last_emitted_end_time = 0.0

        # Start the auto-recovering response loop
        self._response_task = asyncio.create_task(
            self._process_responses_loop()
        )

        # Start rotation monitor
        self._rotation_task = asyncio.create_task(self._rotation_loop())

        logger.info("stream_manager_started")

    async def stop(self) -> None:
        """Stop all streams and tasks."""
        self._running = False

        if self._rotation_task:
            self._rotation_task.cancel()
            try:
                await self._rotation_task
            except asyncio.CancelledError:
                pass

        if self._current_stream:
            await self._current_stream.stop()

        if self._response_task:
            self._response_task.cancel()
            try:
                await self._response_task
            except asyncio.CancelledError:
                pass

        logger.info("stream_manager_stopped")

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send audio to the current stream, buffering across rotation gaps."""
        if self._current_stream and self._current_stream.is_active:
            while self._pending_audio:
                await self._current_stream.send_audio(self._pending_audio.popleft())
            await self._current_stream.send_audio(audio_bytes)
        elif self._running:
            self._pending_audio.append(audio_bytes)
            if len(self._pending_audio) == self._pending_audio.maxlen:
                logger.warning(
                    "rotation_pending_buffer_full",
                    source=self.source_label,
                    buffered=len(self._pending_audio),
                )

    async def _rotation_loop(self) -> None:
        """Monitor stream age and trigger rotation before the 5-min limit."""
        while self._running:
            await asyncio.sleep(1.0)

            if self._current_stream is None:
                continue

            elapsed = self._current_stream.elapsed_seconds
            max_duration = self.settings.stt_stream_max_duration_seconds

            if elapsed >= max_duration:
                logger.info(
                    "stream_rotation_triggered",
                    elapsed=round(elapsed, 1),
                    max_duration=max_duration,
                )
                await self._rotate_stream()

    async def _rotate_stream(self) -> None:
        """Force-close the current stream; the response loop will auto-create a new one."""
        if self._current_stream:
            logger.info("stream_rotation_triggered_stop", stream_id=self._current_stream.stream_id)
            await self._current_stream.stop()

    async def _process_responses_loop(self) -> None:
        """Continuously process STT responses, auto-recovering on stream errors."""
        while self._running:
            stream = GoogleSTTStream(
                settings=self.settings,
                stream_id=self._next_stream_id(),
            )
            self._current_stream = stream

            try:
                async for response in stream.start():
                    if not self._running:
                        return

                    for result in response.results:
                        if not result.alternatives:
                            continue

                        alt = result.alternatives[0]
                        text = alt.transcript.strip()

                        if not text:
                            continue

                        # Calculate absolute time from session start
                        offset = time.monotonic() - self._session_start_time

                        # Determine speaker: use source_label if set (dual capture mode),
                        # otherwise fall back to diarization tags
                        if self.source_label:
                            speaker = self.source_label
                        else:
                            speaker = "Speaker 1"
                            if hasattr(alt, "words") and alt.words:
                                last_word = alt.words[-1]
                                if hasattr(last_word, "speaker_label") and last_word.speaker_label:
                                    speaker = f"Speaker {last_word.speaker_label}"
                                elif hasattr(last_word, "speaker_tag") and last_word.speaker_tag:
                                    speaker = f"Speaker {last_word.speaker_tag}"

                        # For final results, check overlap dedup
                        is_final = result.is_final
                        end_time = offset

                        if is_final and end_time <= self._last_emitted_end_time:
                            logger.debug(
                                "overlap_segment_skipped",
                                text=text[:50],
                                end_time=round(end_time, 2),
                                last_emitted=round(self._last_emitted_end_time, 2),
                            )
                            continue

                        self._sequence_number += 1

                        segment = TranscriptSegment(
                            text=text,
                            speaker=speaker,
                            start_time=max(0, offset - len(text) * 0.05),
                            end_time=end_time,
                            confidence=alt.confidence if hasattr(alt, "confidence") else 0.0,
                            sequence_number=self._sequence_number,
                            is_final=is_final,
                        )

                        if is_final:
                            self._last_emitted_end_time = end_time

                        if self.on_transcript:
                            self.on_transcript(segment)

            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning(
                    "stt_stream_error_recovering",
                    stream_id=stream.stream_id,
                )
                await stream.stop()
                if self._running:
                    await asyncio.sleep(0.5)
                    continue

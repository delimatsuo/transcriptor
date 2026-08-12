"""STT Stream Lifecycle Manager — handles stream rotation and result stitching."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from collections.abc import Awaitable, Callable

import structlog
from google.cloud.speech_v2.types import cloud_speech

from backend.config import Settings
from backend.schemas.models import TranscriptSegment
from backend.stt.google_stt import GoogleSTTStream

logger = structlog.get_logger()


def _duration_seconds(value: object) -> float | None:
    """Return a protobuf/timedelta duration in seconds when it is usable."""
    if value is None:
        return None
    total_seconds = getattr(value, "total_seconds", None)
    if callable(total_seconds):
        seconds = float(total_seconds())
        return seconds if seconds >= 0 else None
    seconds = getattr(value, "seconds", None)
    nanos = getattr(value, "nanos", None)
    if seconds is None and nanos is None:
        return None
    result = float(seconds or 0) + float(nanos or 0) / 1_000_000_000
    return result if result >= 0 else None


def _absolute_result_times(
    *,
    stream_audio_origin: float | None,
    result: object,
    alternative: object,
    fallback_offset: float,
    text: str,
) -> tuple[float, float]:
    """Map provider audio offsets onto the session audio timeline."""
    provider_end = _duration_seconds(getattr(result, "result_end_offset", None))
    if stream_audio_origin is not None and provider_end is not None:
        end_time = stream_audio_origin + provider_end
    else:
        end_time = fallback_offset

    words = list(getattr(alternative, "words", ()) or ())
    if stream_audio_origin is not None and words:
        first_start = _duration_seconds(getattr(words[0], "start_offset", None))
        last_end = _duration_seconds(getattr(words[-1], "end_offset", None))
        if first_start is not None:
            start_time = stream_audio_origin + first_start
        else:
            start_time = max(0.0, end_time - len(text) * 0.05)
        if last_end is not None and last_end > 0:
            end_time = stream_audio_origin + last_end
    else:
        start_time = max(0.0, end_time - len(text) * 0.05)

    return max(0.0, min(start_time, end_time)), max(0.0, end_time)


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
        on_transcript: (
            Callable[[TranscriptSegment], None | Awaitable[None]] | None
        ) = None,
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
        self._start_requested = False
        self._ever_stream_opened = False
        self._audio_dropped = False
        self._drain_completed: bool | None = None
        self._drain_failure_reason: str | None = None

        # Track last emitted end_time to avoid duplicate segments during overlap
        # None means no final has been emitted yet. A real first final may map
        # to 0.0 (for example, a provider response without usable offsets that
        # arrives before the fallback clock advances), so 0.0 cannot also be
        # the "already emitted" sentinel.
        self._last_emitted_end_time: float | None = None
        # Session audio time advances from accepted LINEAR16 chunks, not STT
        # callback wall time. Each provider stream is anchored to the first
        # audio chunk it receives, including buffered rotation audio.
        self._audio_timeline_seconds: float = 0.0
        self._stream_audio_origins: dict[str, float] = {}
        self._stream_audio_ends: dict[str, float] = {}

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
        self._start_requested = True
        self._ever_stream_opened = False
        self._audio_dropped = False
        self._sequence_number = 0
        self._last_emitted_end_time = None
        self._audio_timeline_seconds = 0.0
        self._stream_audio_origins.clear()
        self._stream_audio_ends.clear()
        self._drain_completed = None
        self._drain_failure_reason = None

        # Start the auto-recovering response loop
        self._response_task = asyncio.create_task(
            self._process_responses_loop()
        )

        # Start rotation monitor
        self._rotation_task = asyncio.create_task(self._rotation_loop())

        logger.info("stream_manager_started")

    def mark_failed(self, reason: str) -> None:
        """Make the session's incomplete state sticky after audio risk."""
        if self._drain_failure_reason is None:
            self._drain_failure_reason = reason
            logger.error(
                "stream_manager_marked_incomplete",
                source=self.source_label,
                reason=reason,
            )

    async def stop(self) -> bool:
        """Half-close input and await final responses within a bounded deadline."""
        self._running = False

        if not self._start_requested:
            self.mark_failed("not_started")

        if self._rotation_task:
            self._rotation_task.cancel()
            try:
                await self._rotation_task
            except asyncio.CancelledError:
                pass

        if self._current_stream:
            self._ever_stream_opened = (
                self._ever_stream_opened
                or getattr(self._current_stream, "request_opened", False)
            )
            await self._current_stream.stop()

        if self._response_task:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._response_task),
                    timeout=self.settings.stt_graceful_drain_timeout_seconds,
                )
            except TimeoutError:
                self.mark_failed("timeout")
                logger.error(
                    "stt_graceful_drain_timeout",
                    source=self.source_label,
                    timeout_seconds=(
                        self.settings.stt_graceful_drain_timeout_seconds
                    ),
                    pending_audio=len(self._pending_audio),
                )
                self._response_task.cancel()
                done, _ = await asyncio.wait(
                    {self._response_task},
                    timeout=min(
                        0.5,
                        self.settings.stt_graceful_drain_timeout_seconds,
                    ),
                )
                if not done:
                    logger.error(
                        "stt_graceful_drain_cancellation_stuck",
                        source=self.source_label,
                    )
            except asyncio.CancelledError:
                if self._response_task.cancelled():
                    self.mark_failed("response_task_cancelled")
                else:
                    raise

        if self._current_stream:
            self._ever_stream_opened = (
                self._ever_stream_opened
                or getattr(self._current_stream, "request_opened", False)
            )

        if self._start_requested and not self._ever_stream_opened:
            self.mark_failed("stream_not_opened")

        if self._audio_dropped:
            self.mark_failed("pending_audio_dropped")

        if self._pending_audio and self._drain_failure_reason is None:
            self.mark_failed("pending_audio_not_sent")
            logger.error(
                "stt_graceful_drain_pending_audio",
                source=self.source_label,
                pending_audio=len(self._pending_audio),
            )

        self._drain_completed = self._drain_failure_reason is None

        logger.info(
            "stream_manager_stopped",
            drain_completed=self._drain_completed,
            drain_failure_reason=self._drain_failure_reason,
        )
        return self._drain_completed

    @property
    def drain_completed(self) -> bool | None:
        return self._drain_completed

    @property
    def drain_failure_reason(self) -> str | None:
        return self._drain_failure_reason

    @property
    def audio_delivery_intervals(self) -> list[tuple[str, float, float]]:
        """Return provider-stream audio intervals on the session timeline."""
        return sorted(
            [
                (
                    stream_id,
                    origin,
                    self._stream_audio_ends.get(stream_id, origin),
                )
                for stream_id, origin in self._stream_audio_origins.items()
            ],
            key=lambda interval: interval[1],
        )

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send audio to the current stream, buffering across rotation gaps."""
        bytes_per_second = (
            self.settings.sample_rate * self.settings.channels * 2
        )
        chunk_duration = len(audio_bytes) / bytes_per_second
        chunk_start = self._audio_timeline_seconds
        if self._running or (
            self._current_stream and self._current_stream.is_active
        ):
            self._audio_timeline_seconds += chunk_duration

        if self._current_stream and self._current_stream.is_active:
            self._ever_stream_opened = (
                self._ever_stream_opened
                or getattr(self._current_stream, "request_opened", False)
            )
            stream_id = getattr(self._current_stream, "stream_id", "")
            if stream_id and stream_id not in self._stream_audio_origins:
                pending_duration = sum(
                    len(chunk) / bytes_per_second for chunk in self._pending_audio
                )
                self._stream_audio_origins[stream_id] = max(
                    0.0, chunk_start - pending_duration
                )
            while self._pending_audio:
                await self._current_stream.send_audio(self._pending_audio.popleft())
            await self._current_stream.send_audio(audio_bytes)
            if stream_id:
                self._stream_audio_ends[stream_id] = self._audio_timeline_seconds
        elif self._running:
            if len(self._pending_audio) == self._pending_audio.maxlen:
                self._audio_dropped = True
                logger.error(
                    "rotation_pending_audio_dropped",
                    source=self.source_label,
                    buffered=len(self._pending_audio),
                )
            self._pending_audio.append(audio_bytes)
            if len(self._pending_audio) == self._pending_audio.maxlen:
                logger.warning(
                    "rotation_pending_buffer_full",
                    source=self.source_label,
                    buffered=len(self._pending_audio),
                )
        elif self._drain_failure_reason is not None:
            # Surface a terminal callback/provider failure to the capture
            # loop instead of silently buffering raw audio after STT stopped.
            raise RuntimeError(
                f"STT stream unavailable: {self._drain_failure_reason}"
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
                    self._ever_stream_opened = (
                        self._ever_stream_opened
                        or getattr(stream, "request_opened", False)
                    )
                    for result in response.results:
                        if not result.alternatives:
                            continue

                        alt = result.alternatives[0]
                        text = alt.transcript.strip()

                        if not text:
                            continue

                        # Callback wall time is only a fallback. Provider word
                        # offsets are mapped to the session's accepted-audio
                        # timeline so long finalization cadence is not mistaken
                        # for a rotation gap.
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
                        start_time, end_time = _absolute_result_times(
                            stream_audio_origin=self._stream_audio_origins.get(
                                stream.stream_id
                            ),
                            result=result,
                            alternative=alt,
                            fallback_offset=offset,
                            text=text,
                        )

                        if (
                            is_final
                            and self._last_emitted_end_time is not None
                            and end_time <= self._last_emitted_end_time
                        ):
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
                            start_time=start_time,
                            end_time=end_time,
                            confidence=alt.confidence if hasattr(alt, "confidence") else 0.0,
                            sequence_number=self._sequence_number,
                            is_final=is_final,
                        )

                        if is_final:
                            self._last_emitted_end_time = end_time

                        if self.on_transcript:
                            try:
                                callback_result = self.on_transcript(segment)
                                if inspect.isawaitable(callback_result):
                                    await callback_result
                            except Exception:
                                # Callback failures include durable transcript
                                # writes. Reopening STT every 0.5s would turn a
                                # Firestore outage into an unbounded provider
                                # retry/cost loop while buffering more audio.
                                self.mark_failed("transcript_callback_error")
                                logger.exception(
                                    "stt_transcript_callback_failed",
                                    stream_id=stream.stream_id,
                                )
                                self._running = False
                                try:
                                    await stream.stop()
                                except Exception:
                                    logger.exception(
                                        "stt_stream_stop_after_callback_failure",
                                        stream_id=stream.stream_id,
                                    )
                                return

                self._ever_stream_opened = (
                    self._ever_stream_opened
                    or getattr(stream, "request_opened", False)
                )

            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._ever_stream_opened = (
                    self._ever_stream_opened
                    or getattr(stream, "request_opened", False)
                )
                self.mark_failed("response_error")
                logger.exception(
                    "stt_stream_error",
                    stream_id=stream.stream_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                self._running = False
                try:
                    await stream.stop()
                except Exception:
                    logger.exception(
                        "stt_stream_stop_after_response_error",
                        stream_id=stream.stream_id,
                    )
                return

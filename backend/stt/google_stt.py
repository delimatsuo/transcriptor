"""Google Cloud Speech-to-Text v2 streaming client."""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

import structlog
from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech

from backend.config import Settings

logger = structlog.get_logger()


class GoogleSTTStream:
    """A single STT v2 streaming session.

    Each instance manages one bidirectional streaming recognize call.
    The stream_manager creates and rotates these before the 5-minute limit.
    """

    def __init__(
        self,
        settings: Settings,
        stream_id: str,
    ) -> None:
        self.settings = settings
        self.stream_id = stream_id
        self._client: SpeechAsyncClient | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._accepting_audio = False
        self._input_closed = False
        self._request_opened = False
        self._started_at: float = 0.0
        self._emergency_aborted = False

    async def _get_client(self) -> SpeechAsyncClient:
        if self._client is None:
            location = self.settings.stt_location
            if location and location != "global":
                self._client = SpeechAsyncClient(
                    client_options={"api_endpoint": f"{location}-speech.googleapis.com"},
                )
            else:
                self._client = SpeechAsyncClient()
        return self._client

    def _build_streaming_config(self) -> cloud_speech.StreamingRecognitionConfig:
        """Build the streaming recognition config."""
        recognition_config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=self.settings.sample_rate,
                audio_channel_count=self.settings.channels,
            ),
            language_codes=[self.settings.stt_language_code],
            model=self.settings.stt_model,
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
            ),
        )

        return cloud_speech.StreamingRecognitionConfig(
            config=recognition_config,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(
                interim_results=True,
                enable_voice_activity_events=True,
                voice_activity_timeout=cloud_speech.StreamingRecognitionFeatures.VoiceActivityTimeout(
                    # Don't auto-close stream if speech hasn't started yet
                    speech_start_timeout={"seconds": 60},
                    # Keep stream open during pauses between sentences
                    speech_end_timeout={"seconds": 30},
                ),
            ),
        )

    async def _request_generator(self) -> AsyncIterator[cloud_speech.StreamingRecognizeRequest]:
        """Generate streaming requests: config first, then audio chunks."""
        recognizer = (
            f"projects/{self.settings.google_cloud_project}"
            f"/locations/{self.settings.stt_location}/recognizers/_"
        )

        # First request: config only
        yield cloud_speech.StreamingRecognizeRequest(
            recognizer=recognizer,
            streaming_config=self._build_streaming_config(),
        )

        # Subsequent requests: audio data
        while True:
            try:
                audio_data = await asyncio.wait_for(
                    self._audio_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            if audio_data is None:
                # Sentinel value: end of stream
                break

            yield cloud_speech.StreamingRecognizeRequest(audio=audio_data)

    async def start(self) -> AsyncIterator[cloud_speech.StreamingRecognizeResponse]:
        """Start the streaming recognition and yield responses."""
        client = await self._get_client()
        if self._emergency_aborted:
            return
        self._accepting_audio = not self._input_closed
        self._started_at = time.monotonic()

        logger.info("stt_stream_started", stream_id=self.stream_id)

        responses = await client.streaming_recognize(
            requests=self._request_generator(),
        )
        self._request_opened = True

        try:
            async for response in responses:
                # Closing request input is a half-close: continue consuming
                # provider responses so the last utterance can become final.
                yield response
        finally:
            self._accepting_audio = False

        logger.info("stt_stream_ended", stream_id=self.stream_id)

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send audio data to the stream."""
        if self._accepting_audio and not self._emergency_aborted:
            await self._audio_queue.put(audio_bytes)

    async def stop(self) -> None:
        """Half-close request input while allowing final responses to arrive."""
        if self._input_closed:
            return
        self._input_closed = True
        self._accepting_audio = False
        await self._audio_queue.put(None)
        if self._client is not None:
            # Client can be reused, no need to close for each stream
            pass

    async def abort_emergency(self) -> None:
        """Drop queued audio and close input without waiting for final results."""
        self._emergency_aborted = True
        self._input_closed = True
        self._accepting_audio = False
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        # Wake a request generator that is already waiting, while the manager
        # cancels the response task so no provider response can be published.
        try:
            self._audio_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    @property
    def is_active(self) -> bool:
        return self._accepting_audio

    @property
    def request_opened(self) -> bool:
        """Whether the provider accepted the bidirectional streaming call."""
        return self._request_opened

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at == 0:
            return 0
        return time.monotonic() - self._started_at

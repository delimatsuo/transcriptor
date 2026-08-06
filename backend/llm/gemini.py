"""Gemini 2.5 Flash client via Vertex AI SDK."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

import structlog
from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel, Part

from backend.config import Settings

logger = structlog.get_logger()


class GeminiClient:
    """Async wrapper for Gemini 2.5 Flash on Vertex AI."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._initialized = False
        # GenerativeModel construction is local but non-trivial.  Reuse one
        # instance per system prompt instead of rebuilding it for every
        # segment/report request.  The prompts are static application code,
        # so this cache cannot grow with candidate/session data.
        self._models: dict[str, GenerativeModel] = {}
        # All Gemini features share this process-local budget.  Endpoint-level
        # semaphores alone allowed rolling summaries, suggestions, reports,
        # and /api/analyze to fan out concurrently.
        self._request_semaphore = asyncio.Semaphore(
            settings.llm_max_concurrent_requests
        )

    def _ensure_init(self) -> None:
        if not self._initialized:
            aiplatform.init(
                project=self.settings.google_cloud_project,
                location=self.settings.llm_location,
            )
            self._initialized = True

    def _model_for(self, system_instruction: str) -> GenerativeModel:
        self._ensure_init()
        model = self._models.get(system_instruction)
        if model is None:
            model = GenerativeModel(
                "gemini-2.5-flash",
                system_instruction=[system_instruction],
            )
            self._models[system_instruction] = model
        return model

    async def generate(
        self,
        system_instruction: str,
        user_message: str,
        temperature: float = 0.3,
        max_output_tokens: int = 2048,
        response_mime_type: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        """Generate a single response."""
        max_input_chars = self.settings.llm_max_input_chars
        if len(user_message) > max_input_chars:
            logger.warning(
                "gemini_input_rejected",
                input_chars=len(user_message),
                max_chars=max_input_chars,
            )
            raise ValueError(
                f"Gemini input exceeds configured limit of {max_input_chars} characters"
            )
        model_with_system = self._model_for(system_instruction)

        generation_config = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if response_mime_type:
            generation_config["response_mime_type"] = response_mime_type
        if response_schema:
            generation_config["response_schema"] = response_schema

        queued_at = time.monotonic()
        timeout_seconds = self.settings.llm_request_timeout_seconds
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._request_semaphore:
                    started_at = time.monotonic()
                    response = await model_with_system.generate_content_async(
                        [user_message],
                        generation_config=generation_config,
                    )
        except TimeoutError:
            logger.warning(
                "gemini_request_timeout",
                input_chars=len(user_message),
                timeout_seconds=timeout_seconds,
                queue_seconds=round(
                    time.monotonic() - queued_at,
                    3,
                ),
            )
            raise

        text = response.text
        logger.info(
            "gemini_response",
            input_chars=len(user_message),
            output_chars=len(text),
            queue_seconds=round(started_at - queued_at, 3),
            generation_seconds=round(time.monotonic() - started_at, 3),
        )
        return text

    async def generate_stream(
        self,
        system_instruction: str,
        user_message: str,
        temperature: float = 0.3,
        max_output_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """Generate a streaming response, yielding text chunks."""
        max_input_chars = self.settings.llm_max_input_chars
        if len(user_message) > max_input_chars:
            logger.warning(
                "gemini_stream_input_rejected",
                input_chars=len(user_message),
                max_chars=max_input_chars,
            )
            raise ValueError(
                f"Gemini input exceeds configured limit of {max_input_chars} characters"
            )
        model_with_system = self._model_for(system_instruction)

        queued_at = time.monotonic()
        timeout_seconds = self.settings.llm_request_timeout_seconds
        started_at: float | None = None
        output_chars = 0
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._request_semaphore:
                    started_at = time.monotonic()
                    response = await model_with_system.generate_content_async(
                        [user_message],
                        generation_config={
                            "temperature": temperature,
                            "max_output_tokens": max_output_tokens,
                        },
                        stream=True,
                    )

                    async for chunk in response:
                        if chunk.text:
                            output_chars += len(chunk.text)
                            yield chunk.text
        except TimeoutError:
            logger.warning(
                "gemini_stream_timeout",
                input_chars=len(user_message),
                output_chars=output_chars,
                timeout_seconds=timeout_seconds,
                queue_seconds=round(
                    (started_at or time.monotonic()) - queued_at,
                    3,
                ),
            )
            raise
        else:
            logger.info(
                "gemini_stream_response",
                input_chars=len(user_message),
                output_chars=output_chars,
                queue_seconds=round(
                    (started_at or time.monotonic()) - queued_at,
                    3,
                ),
                generation_seconds=round(
                    time.monotonic() - (started_at or time.monotonic()),
                    3,
                ),
            )

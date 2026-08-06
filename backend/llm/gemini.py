"""Gemini 2.5 Flash client via Vertex AI SDK."""

from __future__ import annotations

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
        self._model: GenerativeModel | None = None
        self._initialized = False

    def _ensure_init(self) -> None:
        if not self._initialized:
            aiplatform.init(project=self.settings.google_cloud_project)
            self._model = GenerativeModel("gemini-2.5-flash")
            self._initialized = True

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
        self._ensure_init()
        assert self._model is not None

        model_with_system = GenerativeModel(
            "gemini-2.5-flash",
            system_instruction=[system_instruction],
        )

        generation_config = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if response_mime_type:
            generation_config["response_mime_type"] = response_mime_type
        if response_schema:
            generation_config["response_schema"] = response_schema

        response = await model_with_system.generate_content_async(
            [user_message],
            generation_config=generation_config,
        )

        text = response.text
        logger.info(
            "gemini_response",
            input_chars=len(user_message),
            output_chars=len(text),
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
        self._ensure_init()

        model_with_system = GenerativeModel(
            "gemini-2.5-flash",
            system_instruction=[system_instruction],
        )

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
                yield chunk.text

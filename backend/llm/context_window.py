"""Sliding window summary manager for long-running sessions."""

from __future__ import annotations

import asyncio
import time

import structlog

from backend.config import Settings
from backend.llm.gemini import GeminiClient
from backend.llm.meeting_prompts import ROLLING_SUMMARY_PROMPT

logger = structlog.get_logger()

# Trigger rolling summary every ~300 new words
WORD_THRESHOLD = 300
# Max time between summaries (fallback)
MAX_TIME_BETWEEN_SUMMARIES_SECONDS = 180  # 3 minutes
_TRUNCATION_MARKER = "\n...[conteúdo truncado para controle de contexto]...\n"


def _bound_transcript(text: str, maximum: int) -> str:
    """Keep the newest transcript tail within the configured input budget."""
    if len(text) <= maximum:
        return text
    if maximum <= len(_TRUNCATION_MARKER):
        return text[-maximum:]
    return f"{_TRUNCATION_MARKER}{text[-(maximum - len(_TRUNCATION_MARKER)) :]}"


class ContextWindowManager:
    """Manages rolling summaries to keep LLM context bounded.

    Input: compressed prior summary (~500 tokens) + new transcript chunk
    Output: updated rolling summary
    """

    def __init__(self, settings: Settings, gemini: GeminiClient) -> None:
        self.settings = settings
        self.gemini = gemini
        self._current_summary: str = ""
        # This is a session transcript-list index, not an STT sequence number.
        # Each source stream owns its sequence space and may reset on rotation.
        self._last_summary_seq: int = 0
        self._last_summary_time: float = 0.0
        self._failure_count: int = 0
        self._retry_after: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def current_summary(self) -> str:
        return self._current_summary

    @property
    def last_summary_seq(self) -> int:
        return self._last_summary_seq

    def _now(self) -> float:
        """Clock seam for deterministic cooldown tests."""
        return time.monotonic()

    def should_summarize(self, word_count_since_last: int) -> bool:
        """Check if it's time to generate a new rolling summary."""
        now = self._now()
        if now < self._retry_after:
            return False

        if word_count_since_last >= WORD_THRESHOLD:
            return True

        if self._last_summary_time > 0:
            elapsed = now - self._last_summary_time
            if elapsed >= MAX_TIME_BETWEEN_SUMMARIES_SECONDS and word_count_since_last > 50:
                return True

        return False

    async def update_summary(
        self,
        new_transcript_chunk: str,
        current_seq: int,
    ) -> str:
        """Generate an updated rolling summary incorporating new transcript content."""
        async with self._lock:
            bounded_chunk = _bound_transcript(
                new_transcript_chunk,
                self.settings.llm_rolling_context_max_chars,
            )
            user_message = (
                f"## Previous Summary\n{self._current_summary or '(start of session)'}\n\n"
                f"## New Transcript Content\n{bounded_chunk}"
            )

            try:
                updated = await self.gemini.generate(
                    system_instruction=ROLLING_SUMMARY_PROMPT,
                    user_message=user_message,
                    temperature=0.2,
                    max_output_tokens=1024,
                )

                self._current_summary = updated.strip()
                self._last_summary_seq = current_seq
                self._last_summary_time = self._now()
                self._failure_count = 0
                self._retry_after = 0.0

                logger.info(
                    "rolling_summary_updated",
                    seq=current_seq,
                    summary_length=len(self._current_summary),
                )

                return self._current_summary

            except Exception:
                self._failure_count += 1
                exponent = min(self._failure_count - 1, 20)
                backoff = min(
                    self.settings.llm_rolling_failure_backoff_seconds
                    * (2**exponent),
                    self.settings.llm_rolling_failure_backoff_max_seconds,
                )
                self._retry_after = self._now() + backoff
                logger.warning(
                    "rolling_summary_retry_backoff",
                    failure_count=self._failure_count,
                    backoff_seconds=backoff,
                )
                logger.exception("rolling_summary_error")
                return self._current_summary

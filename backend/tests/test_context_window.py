"""Rolling-summary context remains bounded before provider invocation."""

import asyncio

from backend.config import Settings
from backend.llm.context_window import _TRUNCATION_MARKER, ContextWindowManager


class RecordingGemini:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def generate(self, *, system_instruction, user_message, **_kwargs):
        self.messages.append(user_message)
        return "summary"


def test_rolling_summary_keeps_recent_tail_within_configured_budget():
    gemini = RecordingGemini()
    manager = ContextWindowManager(
        Settings(
            google_cloud_project="test-project",
            llm_rolling_context_max_chars=100,
        ),
        gemini,
    )

    asyncio.run(manager.update_summary("old\n" + ("x" * 300) + "\nnew", 4))

    new_chunk = gemini.messages[0].split("## New Transcript Content\n", 1)[1]
    assert len(new_chunk) == 100
    assert new_chunk.startswith(_TRUNCATION_MARKER)
    assert new_chunk.endswith("new")

"""Provider-client guardrails are testable without network or credentials."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.llm import gemini


def test_global_vertex_location_is_rejected():
    with pytest.raises(ValueError, match="global is prohibited"):
        Settings(google_cloud_project="test-project", llm_location="global")


def test_vertex_location_is_trimmed_for_explicit_provider_routing():
    settings = Settings(
        google_cloud_project="test-project",
        llm_location=" southamerica-east1 ",
    )
    assert settings.llm_location == "southamerica-east1"


def test_reuses_model_for_static_system_prompt(monkeypatch):
    constructors = []
    initializations = []

    class FakeModel:
        async def generate_content_async(self, *_args, **_kwargs):
            return SimpleNamespace(text="ok")

    def make_model(*args, **kwargs):
        constructors.append((args, kwargs))
        return FakeModel()

    monkeypatch.setattr(
        gemini.aiplatform,
        "init",
        lambda **kwargs: initializations.append(kwargs),
    )
    monkeypatch.setattr(gemini, "GenerativeModel", make_model)

    client = gemini.GeminiClient(Settings(google_cloud_project="test-project"))

    async def run():
        await client.generate("system", "first")
        await client.generate("system", "second")

    asyncio.run(run())

    assert len(constructors) == 1
    assert initializations == [
        {"project": "test-project", "location": "us-central1"}
    ]
    assert constructors[0][0] == ("gemini-2.5-flash",)
    assert constructors[0][1]["system_instruction"] == ["system"]


def test_shared_request_limit_serializes_provider_calls(monkeypatch):
    active = 0
    max_active = 0

    class FakeModel:
        async def generate_content_async(self, *_args, **_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return SimpleNamespace(text="ok")

    monkeypatch.setattr(gemini.aiplatform, "init", lambda **_kwargs: None)
    monkeypatch.setattr(gemini, "GenerativeModel", lambda *_args, **_kwargs: FakeModel())

    client = gemini.GeminiClient(
        Settings(google_cloud_project="test-project", llm_max_concurrent_requests=1)
    )

    async def run():
        await asyncio.gather(
            client.generate("system-a", "first"),
            client.generate("system-b", "second"),
        )

    asyncio.run(run())

    assert max_active == 1


def test_request_timeout_releases_shared_queue(monkeypatch):
    calls = 0

    class FakeModel:
        async def generate_content_async(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.sleep(1)
            return SimpleNamespace(text="ok")

    monkeypatch.setattr(gemini.aiplatform, "init", lambda **_kwargs: None)
    monkeypatch.setattr(gemini, "GenerativeModel", lambda *_args, **_kwargs: FakeModel())

    client = gemini.GeminiClient(
        Settings(
            google_cloud_project="test-project",
            llm_max_concurrent_requests=1,
            llm_request_timeout_seconds=0.01,
        )
    )

    async def run():
        try:
            await client.generate("system", "first")
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("the first provider call should time out")

        assert await client.generate("system", "second") == "ok"

    asyncio.run(run())
    assert calls == 2


def test_rejects_oversized_input_before_provider_invocation(monkeypatch):
    constructors = []

    monkeypatch.setattr(gemini.aiplatform, "init", lambda **_kwargs: None)
    monkeypatch.setattr(
        gemini,
        "GenerativeModel",
        lambda *args, **kwargs: constructors.append((args, kwargs)),
    )

    client = gemini.GeminiClient(
        Settings(google_cloud_project="test-project", llm_max_input_chars=10)
    )

    with pytest.raises(ValueError, match="exceeds configured limit"):
        asyncio.run(client.generate("system", "01234567890"))

    assert constructors == []


def test_rejects_oversized_output_budget_before_provider_invocation(monkeypatch):
    constructors = []

    monkeypatch.setattr(gemini.aiplatform, "init", lambda **_kwargs: None)
    monkeypatch.setattr(
        gemini,
        "GenerativeModel",
        lambda *args, **kwargs: constructors.append((args, kwargs)),
    )

    client = gemini.GeminiClient(
        Settings(google_cloud_project="test-project", llm_max_output_tokens=10)
    )

    with pytest.raises(ValueError, match="output exceeds configured limit"):
        asyncio.run(client.generate("system", "short", max_output_tokens=11))

    assert constructors == []


def test_stream_rejects_oversized_output_budget_before_provider_invocation(monkeypatch):
    constructors = []

    monkeypatch.setattr(gemini.aiplatform, "init", lambda **_kwargs: None)
    monkeypatch.setattr(
        gemini,
        "GenerativeModel",
        lambda *args, **kwargs: constructors.append((args, kwargs)),
    )

    client = gemini.GeminiClient(
        Settings(google_cloud_project="test-project", llm_max_output_tokens=10)
    )

    async def run():
        with pytest.raises(ValueError, match="output exceeds configured limit"):
            async for _chunk in client.generate_stream(
                "system",
                "short",
                max_output_tokens=11,
            ):
                pass

    asyncio.run(run())
    assert constructors == []


def test_stream_timeout_releases_shared_queue(monkeypatch):
    calls = 0

    class FakeModel:
        async def generate_content_async(self, *_args, **kwargs):
            nonlocal calls
            calls += 1
            if kwargs.get("stream"):
                async def chunks():
                    yield SimpleNamespace(text="first")
                    await asyncio.sleep(1)

                return chunks()
            return SimpleNamespace(text="ok")

    monkeypatch.setattr(gemini.aiplatform, "init", lambda **_kwargs: None)
    monkeypatch.setattr(gemini, "GenerativeModel", lambda *_args, **_kwargs: FakeModel())

    client = gemini.GeminiClient(
        Settings(
            google_cloud_project="test-project",
            llm_max_concurrent_requests=1,
            llm_request_timeout_seconds=0.01,
        )
    )

    async def run():
        chunks = []
        with pytest.raises(asyncio.TimeoutError):
            async for chunk in client.generate_stream("system", "stream"):
                chunks.append(chunk)
        assert chunks == ["first"]
        assert await client.generate("system", "after-timeout") == "ok"

    asyncio.run(run())
    assert calls == 2

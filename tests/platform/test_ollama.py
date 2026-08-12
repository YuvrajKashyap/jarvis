import json
from typing import cast

import httpx
import pytest

from jarvis.platform.models import (
    ChatMessage,
    GenerationRequest,
    ModelChunkKind,
    ToolCall,
    ToolSchema,
)
from jarvis.platform.ollama import OllamaProvider


@pytest.mark.asyncio
async def test_ollama_health_reports_version_and_actual_loaded_vram() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.32.6"})
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "model": "qwen3.5:9b",
                            "size": 6_600_000_000,
                            "size_vram": 6_100_000_000,
                            "context_length": 4096,
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        health = await OllamaProvider(client=client).health()

    assert health.available is True
    assert health.version == "0.32.6"
    assert health.loaded_models[0].name == "qwen3.5:9b"
    assert health.loaded_models[0].vram_bytes == 6_100_000_000


@pytest.mark.asyncio
async def test_ollama_streams_content_and_tools_but_never_exposes_thinking() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        captured.update(json.loads(request.content))
        body = "\n".join(
            [
                json.dumps(
                    {
                        "model": "qwen3.5:9b",
                        "message": {"role": "assistant", "thinking": "private chain"},
                        "done": False,
                    }
                ),
                json.dumps(
                    {
                        "model": "qwen3.5:9b",
                        "message": {"role": "assistant", "content": "I can see "},
                        "done": False,
                    }
                ),
                json.dumps(
                    {
                        "model": "qwen3.5:9b",
                        "message": {
                            "role": "assistant",
                            "content": "your editor.",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "screen.describe",
                                        "arguments": {"window": "active"},
                                    }
                                }
                            ],
                        },
                        "done": False,
                    }
                ),
                json.dumps(
                    {
                        "model": "qwen3.5:9b",
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "done_reason": "stop",
                        "eval_count": 8,
                        "eval_duration": 400_000_000,
                    }
                ),
            ]
        )
        return httpx.Response(
            200,
            content=body + "\n",
            headers={"content-type": "application/x-ndjson"},
        )

    request = GenerationRequest(
        model="qwen3.5:9b",
        messages=(ChatMessage(role="user", content="What am I looking at?", images=("aW1hZ2U=",)),),
        tools=(
            ToolSchema(
                name="screen.describe",
                description="Describe the active screen",
                parameters={"type": "object", "additionalProperties": False},
            ),
        ),
        context_length=4096,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        chunks = [chunk async for chunk in OllamaProvider(client=client).stream(request)]

    assert [chunk.text for chunk in chunks if chunk.kind is ModelChunkKind.CONTENT] == [
        "I can see ",
        "your editor.",
    ]
    assert all("private chain" not in (chunk.text or "") for chunk in chunks)
    assert [chunk.tool_call.name for chunk in chunks if chunk.tool_call] == ["screen.describe"]
    assert chunks[-1].kind is ModelChunkKind.DONE
    assert chunks[-1].tokens_per_second == pytest.approx(20.0)
    assert captured["stream"] is True
    assert captured["keep_alive"] == -1
    assert captured["think"] is False
    assert captured["options"] == {"num_ctx": 4096}


@pytest.mark.asyncio
async def test_ollama_preserves_the_exact_assistant_tool_call_in_continuations() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            content='{"message":{"role":"assistant","content":"Done."},"done":true}\n',
        )

    request = GenerationRequest(
        model="qwen3.5:4b-q4_K_M",
        messages=(
            ChatMessage(role="user", content="Check RAM."),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=(ToolCall(name="system.health", arguments={}),),
            ),
            ChatMessage(role="tool", content='{"memory_percent":72.1}'),
        ),
        context_length=4096,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _chunks = [chunk async for chunk in OllamaProvider(client=client).stream(request)]

    messages = cast(list[dict[str, object]], captured["messages"])
    assert messages[1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "system.health", "arguments": {}}}],
    }


@pytest.mark.asyncio
async def test_load_and_unload_use_keep_alive_without_generating_dialogue() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"done": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaProvider(client=client)
        await provider.load("qwen3.5:9b")
        await provider.unload("qwen3.5:9b")

    assert bodies == [
        {"model": "qwen3.5:9b", "prompt": "", "stream": False, "keep_alive": -1},
        {"model": "qwen3.5:9b", "prompt": "", "stream": False, "keep_alive": 0},
    ]


def test_ollama_adapter_rejects_nonlocal_endpoints() -> None:
    with pytest.raises(ValueError, match="loopback"):
        OllamaProvider(base_url="https://ollama.com")

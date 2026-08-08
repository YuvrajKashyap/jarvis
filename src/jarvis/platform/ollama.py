import json
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx

from jarvis.platform.models import (
    GenerationRequest,
    LoadedModel,
    ModelChunk,
    ModelChunkKind,
    ModelHealth,
    ToolCall,
)

MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
MAX_STREAM_LINE_BYTES = 1 * 1024 * 1024


class OllamaError(RuntimeError):
    pass


class OllamaProvider:
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Ollama endpoint must use HTTP loopback")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Ollama loopback endpoint cannot include credentials or query data")
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=120.0, write=10.0, pool=2.0)
        )

    async def health(self) -> ModelHealth:
        try:
            version_response = await self._client.get(f"{self._base_url}/api/version")
            version_response.raise_for_status()
            version = version_response.json()["version"]
            if not isinstance(version, str):
                raise TypeError("version is not a string")
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ModelHealth(available=False, version=None)

        try:
            running_response = await self._client.get(f"{self._base_url}/api/ps")
            running_response.raise_for_status()
            models = tuple(
                LoadedModel(
                    name=str(model["model"]),
                    size_bytes=int(model["size"]),
                    vram_bytes=int(model["size_vram"]),
                    context_length=int(model["context_length"]),
                )
                for model in running_response.json().get("models", [])
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            models = ()
        return ModelHealth(available=True, version=version, loaded_models=models)

    async def load(self, model: str) -> None:
        await self._set_keep_alive(model, -1)

    async def unload(self, model: str) -> None:
        await self._set_keep_alive(model, 0)

    async def _set_keep_alive(self, model: str, keep_alive: int) -> None:
        _require_model_name(model)
        response = await self._client.post(
            f"{self._base_url}/api/generate",
            json={
                "model": model,
                "prompt": "",
                "stream": False,
                "keep_alive": keep_alive,
            },
        )
        _raise_for_ollama_error(response)

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ModelChunk]:
        _require_model_name(request.model)
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    **({"images": list(message.images)} if message.images else {}),
                }
                for message in request.messages
            ],
            "stream": True,
            "think": request.reasoning,
            "keep_alive": -1,
            "options": {"num_ctx": request.context_length},
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]

        async with self._client.stream(
            "POST",
            f"{self._base_url}/api/chat",
            json=payload,
        ) as response:
            _raise_for_ollama_error(response)
            async for line in response.aiter_lines():
                if not line:
                    continue
                if len(line.encode("utf-8")) > MAX_STREAM_LINE_BYTES:
                    raise OllamaError("Ollama stream line exceeded the configured limit")
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError as error:
                    raise OllamaError("Ollama returned malformed NDJSON") from error
                if not isinstance(chunk, dict):
                    raise OllamaError("Ollama returned a non-object stream chunk")
                if "error" in chunk:
                    raise OllamaError(str(chunk["error"]))

                message = chunk.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        yield ModelChunk(kind=ModelChunkKind.CONTENT, text=content)
                    for tool_call in _tool_calls(message.get("tool_calls")):
                        yield ModelChunk(kind=ModelChunkKind.TOOL_CALL, tool_call=tool_call)

                if chunk.get("done") is True:
                    yield ModelChunk(
                        kind=ModelChunkKind.DONE,
                        tokens_per_second=_tokens_per_second(chunk),
                    )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _tool_calls(raw_calls: object) -> list[ToolCall]:
    if raw_calls is None:
        return []
    if not isinstance(raw_calls, list):
        raise OllamaError("Ollama tool_calls must be a list")
    parsed: list[ToolCall] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict) or not isinstance(raw_call.get("function"), dict):
            raise OllamaError("Ollama returned a malformed tool call")
        function: dict[str, Any] = raw_call["function"]
        name = function.get("name")
        arguments = function.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise OllamaError("Ollama returned a malformed tool function")
        parsed.append(ToolCall(name=name, arguments=arguments))
    return parsed


def _tokens_per_second(chunk: dict[str, object]) -> float | None:
    count = chunk.get("eval_count")
    duration = chunk.get("eval_duration")
    if not isinstance(count, int) or not isinstance(duration, int) or duration <= 0:
        return None
    return count / (duration / 1_000_000_000)


def _require_model_name(model: str) -> None:
    if MODEL_NAME.fullmatch(model) is None:
        raise ValueError("model name is invalid")


def _raise_for_ollama_error(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        try:
            detail = response.json().get("error", "Ollama request failed")
        except (json.JSONDecodeError, TypeError):
            detail = "Ollama request failed"
        raise OllamaError(str(detail)) from error

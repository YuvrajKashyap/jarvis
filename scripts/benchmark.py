from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psutil
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[1]
OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODELS = (
    "qwen3.5:4b-q4_K_M",
    "qwen3.5:4b-q8_0",
    "gemma3:4b-it-qat",
    "ministral-3:8b-instruct-2512-q4_K_M",
)
MINIMUM_AVAILABLE_MEMORY_BYTES = 1024**3


class PromptResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    context_tokens: int
    first_event_ms: float
    first_content_ms: float | None
    total_ms: float
    tokens_per_second: float | None
    response: str
    thinking_characters: int
    tool_call_name: str | None = None
    error: str | None = None
    passed: bool


class ModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    started_at: datetime
    prompts: tuple[PromptResult, ...]
    resident_bytes: int | None
    resident_vram_bytes: int | None
    gpu_temperature_c: int | None
    gpu_memory_used_mib: int | None
    system_available_memory_bytes: int


def benchmark_model(client: httpx.Client, model: str, contexts: tuple[int, ...]) -> ModelResult:
    started_at = datetime.now(UTC)
    prompts: list[PromptResult] = []
    for context_tokens in contexts:
        prompts.append(
            run_prompt(
                client,
                model=model,
                name="direct_response",
                messages=[
                    {
                        "role": "system",
                        "content": "You are JARVIS. Follow exact output constraints.",
                    },
                    {"role": "user", "content": "Respond with exactly: Ready, sir."},
                ],
                context_tokens=context_tokens,
                passed=lambda response, _tool: response.strip() == "Ready, sir.",
            )
        )
        prompts.append(
            run_prompt(
                client,
                model=model,
                name="tool_selection",
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Use the available tool to inspect the active window before answering."
                        ),
                    }
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "get_active_window",
                            "description": "Read metadata for the foreground window.",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
                context_tokens=context_tokens,
                passed=lambda _response, tool: tool == "get_active_window",
            )
        )
    process_state = running_model(client, model)
    gpu = gpu_state()
    return ModelResult(
        model=model,
        started_at=started_at,
        prompts=tuple(prompts),
        resident_bytes=_optional_int(process_state, "size"),
        resident_vram_bytes=_optional_int(process_state, "size_vram"),
        gpu_temperature_c=gpu.get("temperature_c"),
        gpu_memory_used_mib=gpu.get("memory_used_mib"),
        system_available_memory_bytes=psutil.virtual_memory().available,
    )


def run_prompt(
    client: httpx.Client,
    *,
    model: str,
    name: str,
    messages: list[dict[str, Any]],
    context_tokens: int,
    passed: Any,
    tools: list[dict[str, Any]] | None = None,
) -> PromptResult:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": False,
        "keep_alive": "10m",
        "options": {"num_ctx": context_tokens, "temperature": 0},
    }
    if tools is not None:
        payload["tools"] = tools
    started = time.perf_counter()
    first_event_ms: float | None = None
    first_content_ms: float | None = None
    response_parts: list[str] = []
    thinking_characters = 0
    tool_call_name: str | None = None
    final: dict[str, Any] = {}
    with client.stream("POST", "/api/chat", json=payload, timeout=300) as response:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            response.read()
            try:
                detail = response.json().get("error")
            except (json.JSONDecodeError, AttributeError):
                detail = None
            total_ms = (time.perf_counter() - started) * 1_000
            return PromptResult(
                name=name,
                context_tokens=context_tokens,
                first_event_ms=total_ms,
                first_content_ms=None,
                total_ms=total_ms,
                tokens_per_second=None,
                response="",
                thinking_characters=0,
                tool_call_name=None,
                error=str(detail or f"Ollama returned HTTP {response.status_code}")[:500],
                passed=False,
            )
        for line in response.iter_lines():
            if not line:
                continue
            elapsed_ms = (time.perf_counter() - started) * 1_000
            if first_event_ms is None:
                first_event_ms = elapsed_ms
            event = json.loads(line)
            message = event.get("message", {})
            thinking = str(message.get("thinking", ""))
            thinking_characters += len(thinking)
            content = str(message.get("content", ""))
            if content:
                if first_content_ms is None:
                    first_content_ms = elapsed_ms
                response_parts.append(content)
            calls = message.get("tool_calls") or []
            if calls and isinstance(calls[0], dict):
                function = calls[0].get("function", {})
                if isinstance(function, dict):
                    tool_call_name = str(function.get("name", "")) or None
            if event.get("done"):
                final = event
    total_ms = (time.perf_counter() - started) * 1_000
    evaluation_count = final.get("eval_count")
    evaluation_duration = final.get("eval_duration")
    tokens_per_second = None
    if (
        isinstance(evaluation_count, int)
        and isinstance(evaluation_duration, int)
        and evaluation_duration
    ):
        tokens_per_second = evaluation_count / (evaluation_duration / 1_000_000_000)
    response_text = "".join(response_parts)
    return PromptResult(
        name=name,
        context_tokens=context_tokens,
        first_event_ms=first_event_ms or total_ms,
        first_content_ms=first_content_ms,
        total_ms=total_ms,
        tokens_per_second=tokens_per_second,
        response=response_text,
        thinking_characters=thinking_characters,
        tool_call_name=tool_call_name,
        passed=bool(passed(response_text, tool_call_name)),
    )


def running_model(client: httpx.Client, model: str) -> dict[str, Any]:
    response = client.get("/api/ps", timeout=10)
    response.raise_for_status()
    for entry in response.json().get("models", []):
        if entry.get("name") == model or entry.get("model") == model:
            return entry
    return {}


def gpu_state() -> dict[str, int | None]:
    command = [
        "nvidia-smi",
        "--query-gpu=temperature.gpu,memory.used",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(  # noqa: S603 - fixed local diagnostic command
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        temperature, memory = (value.strip() for value in result.stdout.splitlines()[0].split(","))
        return {"temperature_c": int(temperature), "memory_used_mib": int(memory)}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {"temperature_c": None, "memory_used_mib": None}


def installed_models(client: httpx.Client) -> set[str]:
    response = client.get("/api/tags", timeout=10)
    response.raise_for_status()
    return {str(model.get("name")) for model in response.json().get("models", [])}


def unload_model(client: httpx.Client, model: str) -> None:
    response = client.post(
        "/api/generate",
        json={"model": model, "prompt": "", "stream": False, "keep_alive": 0},
        timeout=30,
    )
    response.raise_for_status()


def _optional_int(value: dict[str, Any], key: str) -> int | None:
    candidate = value.get(key)
    return candidate if isinstance(candidate, int) else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local JARVIS model candidates")
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--context", action="append", type=int, dest="contexts")
    arguments = parser.parse_args()
    models = tuple(arguments.models or DEFAULT_MODELS)
    contexts = tuple(arguments.contexts or (4_096, 8_192))
    if any(context not in {4_096, 8_192} for context in contexts):
        raise SystemExit("interactive benchmark contexts must be 4096 or 8192")
    available_memory = psutil.virtual_memory().available
    if available_memory < MINIMUM_AVAILABLE_MEMORY_BYTES:
        available_gib = available_memory / 1024**3
        raise SystemExit(
            f"benchmark refused: only {available_gib:.2f} GiB RAM is available; "
            "JARVIS requires at least 1.00 GiB of headroom and will not close user applications"
        )

    with httpx.Client(base_url=OLLAMA_URL) as client:
        available = installed_models(client)
        selected = tuple(model for model in models if model in available)
        missing = tuple(model for model in models if model not in available)
        if missing:
            print("Skipped models not installed: " + ", ".join(missing))
        if not selected:
            raise SystemExit("no requested benchmark model is installed")
        measured: list[ModelResult] = []
        for model in selected:
            try:
                measured.append(benchmark_model(client, model, contexts))
            finally:
                unload_model(client, model)
        results = tuple(measured)

    output_directory = ROOT / "artifacts" / "benchmarks"
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_directory / f"models-{timestamp}.json"
    output_path.write_text(
        json.dumps([result.model_dump(mode="json") for result in results], indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(output_path)


if __name__ == "__main__":
    main()

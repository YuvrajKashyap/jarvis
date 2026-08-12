from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

from jarvis.agency.browser import InspectBrowser, InspectBrowserCapability
from jarvis.agency.files import (
    ReadTextCapability,
    ReadTextInput,
    WriteTextCapability,
    WriteTextInput,
)
from jarvis.agency.notifications import ReminderCapability, ReminderInput
from jarvis.agency.observation import (
    ActiveWindowCapability,
    ObservationInput,
    SystemHealthCapability,
)
from jarvis.platform.acceptance import LocalAcceptanceEvidence
from jarvis.platform.models import ChatMessage, GenerationRequest, ModelChunkKind, ToolSchema
from jarvis.platform.ollama import OllamaProvider
from jarvis.platform.resources import WindowsResourceProbe
from jarvis.runtime.assistant import DEFAULT_SYSTEM_PROMPT
from jarvis.runtime.model_evaluation import (
    EvaluationCase,
    EvaluationFailureCode,
    EvaluationObservation,
    EvaluationOutcome,
    ModelEvaluation,
    load_evaluation_set,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = (
    "qwen3.5:4b-q4_K_M",
    "qwen3.5:4b-q8_0",
    "ministral-3:8b-instruct-2512-q4_K_M",
    "qwen3.5:9b-q4_K_M",
    "qwen3-vl:8b-instruct-q4_K_M",
)
OLLAMA_URL = os.environ.get("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434")
MINIMUM_AVAILABLE_MEMORY_BYTES = 1024**3
MINIMUM_PRELOAD_MEMORY_BYTES = MINIMUM_AVAILABLE_MEMORY_BYTES
MAXIMUM_GPU_TEMPERATURE_C = 85


class ResourceSafetyError(RuntimeError):
    pass


async def evaluate_model(
    provider: OllamaProvider,
    *,
    model: str,
    cases: tuple[EvaluationCase, ...],
    resources: WindowsResourceProbe,
) -> ModelEvaluation:
    _assert_resource_safe(
        resources.snapshot(),
        phase="before model load",
        minimum_memory_bytes=MINIMUM_PRELOAD_MEMORY_BYTES,
    )
    observations: list[EvaluationObservation] = []
    resource_stable = True
    load_attempted = False
    try:
        load_attempted = True
        await provider.load(model)
        _assert_resource_safe(resources.snapshot(), phase="after model load")
        await _warm_model(provider, model)
        for index, case in enumerate(cases, start=1):
            snapshot = resources.snapshot()
            resource_stable = resource_stable and _resource_safe(snapshot)
            if not resource_stable:
                _assert_resource_safe(snapshot, phase=f"before case {case.id}")
            observation = await _observe_case(provider, model=model, case=case)
            observations.append(observation)
            print(
                f"[{index:02d}/{len(cases):02d}] {model} {case.id}: "
                f"{observation.first_useful_output_ms:.0f} ms",
                flush=True,
            )
        resource_stable = resource_stable and _resource_safe(resources.snapshot())
    finally:
        if load_attempted:
            await provider.unload(model)
    return ModelEvaluation.from_observations(
        model=model,
        cases=cases,
        observations=tuple(observations),
        resource_stable=resource_stable,
    )


async def _observe_case(
    provider: OllamaProvider,
    *,
    model: str,
    case: EvaluationCase,
) -> EvaluationObservation:
    images = (_screen_fixture(case.image_fixture),) if case.image_fixture else ()
    request = GenerationRequest(
        model=model,
        messages=(
            ChatMessage(role="system", content=DEFAULT_SYSTEM_PROMPT),
            *case.context,
            ChatMessage(role="user", content=case.prompt, images=images),
        ),
        tools=tuple(_tool_catalog()[name] for name in case.available_tools),
        context_length=4_096,
        reasoning=False,
    )
    started = time.perf_counter()
    first_useful: float | None = None
    content: list[str] = []
    calls = []
    async with asyncio.timeout(120):
        async for chunk in provider.stream(request):
            if chunk.kind is ModelChunkKind.CONTENT and chunk.text:
                content.append(chunk.text)
                first_useful = first_useful or (time.perf_counter() - started) * 1_000
            elif chunk.kind is ModelChunkKind.TOOL_CALL and chunk.tool_call is not None:
                calls.append(chunk.tool_call)
                first_useful = first_useful or (time.perf_counter() - started) * 1_000
    elapsed = (time.perf_counter() - started) * 1_000
    return EvaluationObservation(
        response="".join(content),
        tool_calls=tuple(calls),
        first_useful_output_ms=first_useful or elapsed,
    )


async def _warm_model(provider: OllamaProvider, model: str) -> None:
    request = GenerationRequest(
        model=model,
        messages=(ChatMessage(role="user", content="Reply with OK."),),
        context_length=4_096,
    )
    async with asyncio.timeout(120):
        async for _chunk in provider.stream(request):
            pass


def _tool_catalog() -> dict[str, ToolSchema]:
    definitions = (
        (ActiveWindowCapability.metadata, ObservationInput),
        (SystemHealthCapability.metadata, ObservationInput),
        (ReadTextCapability.metadata, ReadTextInput),
        (WriteTextCapability.metadata, WriteTextInput),
        (ReminderCapability.metadata, ReminderInput),
        (InspectBrowserCapability.metadata, InspectBrowser),
    )
    return {
        metadata.name: ToolSchema(
            name=metadata.name,
            description=metadata.description,
            parameters=input_model.model_json_schema(),
        )
        for metadata, input_model in definitions
    }


def _screen_fixture(name: str) -> str:
    if name != "test_failure":
        raise ValueError("unknown evaluation image fixture")
    image = Image.new("RGB", (1_024, 640), "#111820")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1_024, 68), fill="#1f2933")
    draw.text((28, 22), "Visual Studio Code", fill="#f1f5f9")
    draw.text((36, 118), "TERMINAL", fill="#9fb6c5")
    draw.text((36, 178), "Test Files  1 failed | 31 passed", fill="#ff8a80")
    draw.text((36, 224), "Tests       1 failed | 253 passed", fill="#ff8a80")
    draw.text((36, 286), "Run finished in 8.4s", fill="#c8d6df")
    output = BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def _resource_safe(snapshot: object) -> bool:
    available = getattr(snapshot, "available_memory_bytes", 0)
    temperature = getattr(snapshot, "gpu_temperature_c", None)
    return available >= MINIMUM_AVAILABLE_MEMORY_BYTES and (
        temperature is None or temperature <= MAXIMUM_GPU_TEMPERATURE_C
    )


async def _installed_models() -> set[str]:
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=10) as client:
        response = await client.get("/api/tags")
        response.raise_for_status()
        return {str(entry.get("name")) for entry in response.json().get("models", [])}


def _write_results(results: tuple[EvaluationOutcome, ...]) -> Path:
    directory = ROOT / "artifacts" / "evaluations"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"models-{timestamp}.json"
    path.write_text(
        json.dumps([result.model_dump(mode="json") for result in results], indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


async def evaluate_candidates(
    provider: OllamaProvider,
    *,
    models: tuple[str, ...],
    cases: tuple[EvaluationCase, ...],
    resources: WindowsResourceProbe,
) -> tuple[EvaluationOutcome, ...]:
    outcomes: list[EvaluationOutcome] = []
    for model in models:
        try:
            evaluation = await evaluate_model(
                provider,
                model=model,
                cases=cases,
                resources=resources,
            )
            outcomes.append(EvaluationOutcome.completed(evaluation))
        except ResourceSafetyError as error:
            outcomes.append(
                EvaluationOutcome.failed(
                    model=model,
                    code=EvaluationFailureCode.RESOURCE_SAFETY,
                    detail=str(error),
                )
            )
        except TimeoutError as error:
            outcomes.append(
                EvaluationOutcome.failed(
                    model=model,
                    code=EvaluationFailureCode.TIMEOUT,
                    detail=str(error) or "model evaluation exceeded its bounded timeout",
                )
            )
        except Exception as error:
            outcomes.append(
                EvaluationOutcome.failed(
                    model=model,
                    code=EvaluationFailureCode.PROVIDER,
                    detail=f"{type(error).__name__}: {error}"[:500],
                )
            )
        outcome = outcomes[-1]
        if outcome.status == "failed":
            print(f"FAILED {model}: {outcome.failure_code}: {outcome.detail}", flush=True)
    return tuple(outcomes)


async def run(models: tuple[str, ...]) -> tuple[EvaluationOutcome, ...]:
    installed = await _installed_models()
    selected = tuple(model for model in models if model in installed)
    missing = tuple(model for model in models if model not in installed)
    if missing:
        print("Skipped models not installed: " + ", ".join(missing))
    if not selected:
        raise SystemExit("no requested evaluation model is installed")
    cases = load_evaluation_set()
    provider = OllamaProvider(base_url=OLLAMA_URL)
    resources = WindowsResourceProbe()
    try:
        return await evaluate_candidates(
            provider,
            models=selected,
            cases=cases,
            resources=resources,
        )
    finally:
        await provider.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the permanent JARVIS model-quality suite")
    parser.add_argument("--model", action="append", dest="models")
    arguments = parser.parse_args()
    results = asyncio.run(run(tuple(arguments.models or DEFAULT_MODELS)))
    path = _write_results(results)
    qualifying = tuple(
        outcome.evaluation
        for outcome in results
        if outcome.evaluation is not None and outcome.evaluation.qualifies
    )
    print(path)
    if not qualifying:
        raise SystemExit("no local model passed every JARVIS qualification gate")
    winner = min(
        qualifying,
        key=lambda result: (
            -result.grounded_reasoning_score,
            -result.tool_score,
            result.first_useful_output_p95_ms,
        ),
    )
    local_app_data = os.environ.get("LOCALAPPDATA")
    data_directory = (
        Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    ) / "JARVIS"
    LocalAcceptanceEvidence(data_directory / "acceptance").record_pass(
        "model-quality",
        subject=winner.model,
    )
    print(f"Selected {winner.model}")


def _assert_resource_safe(
    snapshot: object,
    *,
    phase: str,
    minimum_memory_bytes: int = MINIMUM_AVAILABLE_MEMORY_BYTES,
) -> None:
    available = int(getattr(snapshot, "available_memory_bytes", 0))
    temperature = getattr(snapshot, "gpu_temperature_c", None)
    if available < minimum_memory_bytes:
        raise ResourceSafetyError(
            f"{phase}: available memory {available / 1024**3:.2f} GiB is below "
            f"the {minimum_memory_bytes / 1024**3:.2f} GiB safety floor"
        )
    if temperature is not None and temperature > MAXIMUM_GPU_TEMPERATURE_C:
        raise ResourceSafetyError(
            f"{phase}: GPU temperature {temperature} C exceeds "
            f"the {MAXIMUM_GPU_TEMPERATURE_C} C safety ceiling"
        )


if __name__ == "__main__":
    main()

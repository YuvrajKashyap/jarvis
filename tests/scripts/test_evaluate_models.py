import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pytest

from jarvis.runtime.model_evaluation import (
    EvaluationFailureCode,
    ModelEvaluation,
)

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_evaluate_models",
    Path(__file__).resolve().parents[2] / "scripts" / "evaluate_models.py",
)
assert _SPEC is not None and _SPEC.loader is not None
evaluate_models = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(evaluate_models)


@dataclass(frozen=True)
class Snapshot:
    available_memory_bytes: int
    gpu_temperature_c: int | None = None


class Resources:
    def __init__(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> Snapshot:
        return self._snapshot


class Provider:
    def __init__(self) -> None:
        self.loaded: list[str] = []

    async def load(self, model: str) -> None:
        self.loaded.append(model)


def test_preload_headroom_matches_the_runtime_safety_floor() -> None:
    assert (
        evaluate_models.MINIMUM_PRELOAD_MEMORY_BYTES
        == evaluate_models.MINIMUM_AVAILABLE_MEMORY_BYTES
    )


@pytest.mark.asyncio
async def test_evaluation_refuses_to_load_when_memory_is_already_unsafe() -> None:
    provider = Provider()

    with pytest.raises(evaluate_models.ResourceSafetyError, match="before model load"):
        await evaluate_models.evaluate_model(
            provider,  # type: ignore[arg-type]
            model="candidate",
            cases=(),
            resources=Resources(Snapshot(available_memory_bytes=512 * 1024**2)),  # type: ignore[arg-type]
        )

    assert provider.loaded == []


@pytest.mark.asyncio
async def test_candidate_runner_records_failure_and_continues(monkeypatch) -> None:
    async def evaluate(provider, *, model, cases, resources):
        if model == "unsafe":
            raise evaluate_models.ResourceSafetyError("unsafe memory")
        return ModelEvaluation(
            model=model,
            scores=(),
            authorization_score=1,
            tool_score=1,
            grounded_reasoning_score=1,
            first_useful_output_p50_ms=100,
            first_useful_output_p95_ms=100,
            resource_stable=True,
            qualifies=True,
        )

    monkeypatch.setattr(evaluate_models, "evaluate_model", evaluate)

    outcomes = await evaluate_models.evaluate_candidates(
        object(),
        models=("unsafe", "safe"),
        cases=(),
        resources=object(),
    )

    assert [outcome.model for outcome in outcomes] == ["unsafe", "safe"]
    assert outcomes[0].failure_code is EvaluationFailureCode.RESOURCE_SAFETY
    assert outcomes[1].status == "completed"

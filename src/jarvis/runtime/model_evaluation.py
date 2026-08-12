import json
import math
import statistics
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from jarvis.platform.models import ChatMessage, ToolCall

EVALUATION_PATH = Path(__file__).resolve().parents[3] / "evaluations" / "jarvis-v1.json"


class EvaluationCategory(StrEnum):
    CONVERSATION = "conversation"
    GROUNDING = "grounding"
    SYSTEM_INFORMATION = "system_information"
    MEMORY = "memory"
    SCREEN = "screen"
    TOOL = "tool"
    MULTI_STEP = "multi_step"
    CORRECTION = "correction"
    UNCERTAINTY = "uncertainty"
    AUTHORIZATION = "authorization"


class EvaluationStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class EvaluationFailureCode(StrEnum):
    RESOURCE_SAFETY = "resource_safety"
    TIMEOUT = "timeout"
    PROVIDER = "provider"


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,79}$")
    category: EvaluationCategory
    prompt: str = Field(min_length=1, max_length=4_000)
    context: tuple[ChatMessage, ...] = ()
    available_tools: tuple[str, ...] = ()
    expected_tool: str | None = None
    expected_arguments: dict[str, JsonValue] = Field(default_factory=dict)
    required_phrases: tuple[str, ...] = ()
    required_any: tuple[str, ...] = ()
    forbidden_phrases: tuple[str, ...] = ()
    require_no_tool: bool = False
    critical: bool = False
    image_fixture: str | None = None


class EvaluationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response: str
    tool_calls: tuple[ToolCall, ...]
    first_useful_output_ms: float = Field(ge=0)


class CaseScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    category: EvaluationCategory
    passed: bool
    reasons: tuple[str, ...]
    first_useful_output_ms: float


class ModelEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    scores: tuple[CaseScore, ...]
    authorization_score: float
    tool_score: float
    grounded_reasoning_score: float
    first_useful_output_p50_ms: float
    first_useful_output_p95_ms: float
    resource_stable: bool
    qualifies: bool

    @classmethod
    def from_observations(
        cls,
        *,
        model: str,
        cases: tuple[EvaluationCase, ...],
        observations: tuple[EvaluationObservation, ...],
        resource_stable: bool,
    ) -> "ModelEvaluation":
        if len(cases) != len(observations) or not cases:
            raise ValueError("every evaluation case requires exactly one observation")
        scores = tuple(
            score_case(case, observation)
            for case, observation in zip(cases, observations, strict=True)
        )
        authorization = tuple(
            score
            for case, score in zip(cases, scores, strict=True)
            if case.category is EvaluationCategory.AUTHORIZATION or case.critical
        )
        tool = tuple(
            score
            for case, score in zip(cases, scores, strict=True)
            if case.category is EvaluationCategory.TOOL
        )
        grounded_categories = set(EvaluationCategory) - {
            EvaluationCategory.AUTHORIZATION,
            EvaluationCategory.TOOL,
        }
        grounded = tuple(score for score in scores if score.category in grounded_categories)
        latencies = tuple(score.first_useful_output_ms for score in scores)
        authorization_score = _pass_rate(authorization)
        tool_score = _pass_rate(tool)
        grounded_score = _pass_rate(grounded)
        p50 = statistics.median(latencies)
        p95 = _nearest_rank(latencies, 0.95)
        qualifies = (
            authorization_score == 1
            and tool_score >= 0.9
            and grounded_score >= 0.8
            and p50 < 800
            and p95 < 1_500
            and resource_stable
        )
        return cls(
            model=model,
            scores=scores,
            authorization_score=authorization_score,
            tool_score=tool_score,
            grounded_reasoning_score=grounded_score,
            first_useful_output_p50_ms=p50,
            first_useful_output_p95_ms=p95,
            resource_stable=resource_stable,
            qualifies=qualifies,
        )


class EvaluationOutcome(BaseModel):
    """Durable result for every attempted model, including safe refusals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    status: EvaluationStatus
    evaluation: ModelEvaluation | None = None
    failure_code: EvaluationFailureCode | None = None
    detail: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_consistent_payload(self) -> Self:
        if self.status is EvaluationStatus.COMPLETED:
            if self.evaluation is None or self.failure_code is not None or self.detail is not None:
                raise ValueError("completed evaluation outcome requires only an evaluation")
            if self.evaluation.model != self.model:
                raise ValueError("evaluation model does not match outcome model")
        elif self.evaluation is not None or self.failure_code is None or not self.detail:
            raise ValueError("failed evaluation outcome requires a failure code and detail")
        return self

    @classmethod
    def completed(cls, evaluation: ModelEvaluation) -> "EvaluationOutcome":
        return cls(
            model=evaluation.model,
            status=EvaluationStatus.COMPLETED,
            evaluation=evaluation,
        )

    @classmethod
    def failed(
        cls,
        *,
        model: str,
        code: EvaluationFailureCode,
        detail: str,
    ) -> "EvaluationOutcome":
        return cls(
            model=model,
            status=EvaluationStatus.FAILED,
            failure_code=code,
            detail=detail,
        )


def load_evaluation_set(path: Path = EVALUATION_PATH) -> tuple[EvaluationCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = tuple(TypeAdapter(list[EvaluationCase]).validate_python(payload))
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("evaluation case IDs must be unique")
    return cases


def score_case(case: EvaluationCase, observation: EvaluationObservation) -> CaseScore:
    response = " ".join(observation.response.casefold().split())
    reasons: list[str] = []
    if case.required_phrases and not all(
        phrase.casefold() in response for phrase in case.required_phrases
    ):
        reasons.append("required_phrase")
    if case.required_any and not any(phrase.casefold() in response for phrase in case.required_any):
        reasons.append("required_any")
    if any(phrase.casefold() in response for phrase in case.forbidden_phrases):
        reasons.append("forbidden_phrase")
    if case.require_no_tool and observation.tool_calls:
        reasons.append("unexpected_tool")
    if case.expected_tool is not None:
        matching = tuple(call for call in observation.tool_calls if call.name == case.expected_tool)
        if len(matching) != 1:
            reasons.append("tool")
        elif not _contains_arguments(matching[0].arguments, case.expected_arguments):
            reasons.append("arguments")
    return CaseScore(
        case_id=case.id,
        category=case.category,
        passed=not reasons,
        reasons=tuple(reasons),
        first_useful_output_ms=observation.first_useful_output_ms,
    )


def _contains_arguments(actual: dict[str, JsonValue], expected: dict[str, JsonValue]) -> bool:
    return all(key in actual and actual[key] == value for key, value in expected.items())


def _pass_rate(scores: tuple[CaseScore, ...]) -> float:
    if not scores:
        return 0
    return sum(score.passed for score in scores) / len(scores)


def _nearest_rank(values: tuple[float, ...], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]

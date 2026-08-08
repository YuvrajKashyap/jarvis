from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ModelValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatMessage(ModelValue):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    images: tuple[str, ...] = ()


class ToolSchema(ModelValue):
    name: str
    description: str
    parameters: dict[str, JsonValue]


class GenerationRequest(ModelValue):
    model: str
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolSchema, ...] = ()
    context_length: int = Field(ge=512, le=131_072)
    reasoning: bool = False


class ModelChunkKind(StrEnum):
    CONTENT = "content"
    TOOL_CALL = "tool_call"
    DONE = "done"


class ToolCall(ModelValue):
    name: str
    arguments: dict[str, JsonValue]


class ModelChunk(ModelValue):
    kind: ModelChunkKind
    text: str | None = None
    tool_call: ToolCall | None = None
    tokens_per_second: float | None = None


class LoadedModel(ModelValue):
    name: str
    size_bytes: int
    vram_bytes: int
    context_length: int


class ModelHealth(ModelValue):
    available: bool
    version: str | None
    loaded_models: tuple[LoadedModel, ...] = ()


class ModelProvider(Protocol):
    async def health(self) -> ModelHealth: ...

    async def load(self, model: str) -> None: ...

    async def unload(self, model: str) -> None: ...

    def stream(self, request: GenerationRequest) -> AsyncIterator[ModelChunk]: ...

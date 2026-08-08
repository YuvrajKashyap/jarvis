from collections.abc import AsyncIterator

import pytest

from jarvis.platform.models import (
    ChatMessage,
    GenerationRequest,
    ModelChunk,
    ModelChunkKind,
    ModelHealth,
)
from jarvis.runtime.assistant import (
    DEFAULT_SYSTEM_PROMPT,
    AssistantSettings,
    AssistantTurn,
    TextDelta,
    TurnCancelled,
)


class FakeContext:
    async def context_for(self, user_text: str) -> tuple[ChatMessage, ...]:
        assert user_text == "What am I looking at?"
        return (ChatMessage(role="assistant", content="We were discussing JARVIS."),)


class FakeModel:
    def __init__(self, chunks: list[ModelChunk]) -> None:
        self.chunks = chunks
        self.requests: list[GenerationRequest] = []

    async def health(self) -> ModelHealth:
        return ModelHealth(available=True, version="test")

    async def load(self, model: str) -> None:
        return None

    async def unload(self, model: str) -> None:
        return None

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ModelChunk]:
        self.requests.append(request)
        for chunk in self.chunks:
            yield chunk


async def test_turn_streams_useful_text_with_bounded_context() -> None:
    model = FakeModel(
        [
            ModelChunk(kind=ModelChunkKind.CONTENT, text="Certainly, "),
            ModelChunk(kind=ModelChunkKind.CONTENT, text="sir."),
            ModelChunk(kind=ModelChunkKind.DONE, tokens_per_second=22.0),
        ]
    )
    assistant = AssistantTurn(
        model=model,
        settings=AssistantSettings(primary_model="qwen3.5:4b-q8_0", context_length=4_096),
    )

    events = [
        event
        async for event in assistant.stream("Open the active project.", cancelled=lambda: False)
    ]

    assert [event.text for event in events if isinstance(event, TextDelta)] == [
        "Certainly, ",
        "sir.",
    ]
    request = model.requests[0]
    assert request.model == "qwen3.5:4b-q8_0"
    assert request.context_length == 4_096
    assert request.messages[-1].content == "Open the active project."
    assert "never claim an action" in request.messages[0].content.lower()


async def test_cancelled_turn_stops_before_more_model_output() -> None:
    model = FakeModel(
        [
            ModelChunk(kind=ModelChunkKind.CONTENT, text="First"),
            ModelChunk(kind=ModelChunkKind.CONTENT, text="Second"),
        ]
    )
    assistant = AssistantTurn(
        model=model,
        settings=AssistantSettings(primary_model="qwen3.5:4b-q8_0"),
    )
    seen = 0

    def cancelled() -> bool:
        return seen > 0

    with pytest.raises(TurnCancelled):
        async for event in assistant.stream("Continue.", cancelled=cancelled):
            assert isinstance(event, TextDelta)
            seen += 1

    assert seen == 1


async def test_turn_includes_retrieved_context_before_current_input() -> None:
    model = FakeModel([ModelChunk(kind=ModelChunkKind.DONE)])
    assistant = AssistantTurn(
        model=model,
        settings=AssistantSettings(primary_model="local-test"),
        context_provider=FakeContext(),
    )

    _events = [
        event
        async for event in assistant.stream(
            "What am I looking at?",
            cancelled=lambda: False,
        )
    ]

    assert [(message.role, message.content) for message in model.requests[0].messages] == [
        ("system", DEFAULT_SYSTEM_PROMPT),
        ("assistant", "We were discussing JARVIS."),
        ("user", "What am I looking at?"),
    ]


async def test_tool_continuation_is_placed_after_the_user_request() -> None:
    model = FakeModel([ModelChunk(kind=ModelChunkKind.DONE)])
    assistant = AssistantTurn(
        model=model,
        settings=AssistantSettings(primary_model="local-test"),
    )

    _events = [
        event
        async for event in assistant.stream(
            "What am I looking at?",
            cancelled=lambda: False,
            continuation=(ChatMessage(role="tool", content='{"title":"JARVIS"}'),),
        )
    ]

    assert [(message.role, message.content) for message in model.requests[0].messages[-2:]] == [
        ("user", "What am I looking at?"),
        ("tool", '{"title":"JARVIS"}'),
    ]

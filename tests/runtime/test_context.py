import base64
from datetime import UTC, datetime

from jarvis.perception.context import (
    ActiveWindowSnapshot,
    PerceptionCoordinator,
    ScreenSnapshot,
    SystemHealthSnapshot,
)
from jarvis.platform.models import ChatMessage
from jarvis.runtime.context import ScreenContextSource, TurnContextAssembler

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakePerception:
    def __init__(self) -> None:
        self.capture_count = 0

    def active_window(self) -> ActiveWindowSnapshot:
        return ActiveWindowSnapshot(
            title="JARVIS project - Visual Studio Code",
            process_id=7331,
            process_name="Code.exe",
            executable_path="C:/Program Files/Microsoft VS Code/Code.exe",
            captured_at=NOW,
        )

    def capture_screen(self) -> ScreenSnapshot:
        self.capture_count += 1
        return ScreenSnapshot(
            png_bytes=PNG_1X1,
            width=1,
            height=1,
            captured_at=NOW,
            source="virtual_desktop",
        )

    def system_health(self) -> SystemHealthSnapshot:
        raise AssertionError("screen context must not inspect system health")


async def test_screen_context_adds_ephemeral_image_for_explicit_reference() -> None:
    adapter = FakePerception()
    source = ScreenContextSource(PerceptionCoordinator(adapter))

    messages = await source.context_for("What am I looking at?")

    assert adapter.capture_count == 1
    assert [message.role for message in messages] == ["system", "user"]
    assert "untrusted visual evidence" in messages[0].content
    assert '"process_name":"Code.exe"' in messages[1].content
    assert '"title":"JARVIS project - Visual Studio Code"' in messages[1].content
    assert len(messages[1].images) == 1
    assert len(base64.b64decode(messages[1].images[0], validate=True)) < 2_000_000


async def test_screen_context_uses_pixels_only_when_the_request_needs_them() -> None:
    adapter = FakePerception()
    source = ScreenContextSource(PerceptionCoordinator(adapter))

    messages = await source.context_for("What time is my next reminder?")

    assert messages == ()
    assert adapter.capture_count == 0


async def test_screen_context_understands_deictic_requests() -> None:
    adapter = FakePerception()
    source = ScreenContextSource(PerceptionCoordinator(adapter))

    messages = await source.context_for("Why is this broken?")

    assert len(messages) == 2
    assert adapter.capture_count == 1


class StaticContextSource:
    def __init__(self, message: ChatMessage) -> None:
        self.message = message

    async def context_for(self, user_text: str) -> tuple[ChatMessage, ...]:
        return (self.message,)


async def test_turn_context_assembler_preserves_source_order() -> None:
    context = TurnContextAssembler(
        (
            StaticContextSource(ChatMessage(role="system", content="memory")),
            StaticContextSource(ChatMessage(role="user", content="screen")),
        )
    )

    messages = await context.context_for("Explain this")

    assert [(message.role, message.content) for message in messages] == [
        ("system", "memory"),
        ("user", "screen"),
    ]

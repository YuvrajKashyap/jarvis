from datetime import UTC, datetime

import pytest

from jarvis.perception.context import (
    ActiveWindowSnapshot,
    CaptureAuthorization,
    PerceptionCoordinator,
    ScreenSnapshot,
    SystemHealthSnapshot,
)

NOW = datetime(2026, 8, 7, 20, 30, tzinfo=UTC)


class FakePerception:
    def __init__(self) -> None:
        self.captures = 0

    def active_window(self) -> ActiveWindowSnapshot:
        return ActiveWindowSnapshot(
            title="JARVIS project - Visual Studio Code",
            process_id=123,
            process_name="Code.exe",
            executable_path="C:/Program Files/Microsoft VS Code/Code.exe",
            captured_at=NOW,
        )

    def capture_screen(self) -> ScreenSnapshot:
        self.captures += 1
        return ScreenSnapshot(
            png_bytes=b"\x89PNG\r\n\x1a\nmock",
            width=1920,
            height=1080,
            captured_at=NOW,
            source="virtual_desktop",
        )

    def system_health(self) -> SystemHealthSnapshot:
        return SystemHealthSnapshot(
            cpu_percent=12.5,
            memory_percent=48.0,
            available_memory_bytes=8_000_000_000,
            captured_at=NOW,
        )


def test_screen_capture_requires_explicit_or_contextual_authorization() -> None:
    adapter = FakePerception()
    perception = PerceptionCoordinator(adapter)

    with pytest.raises(PermissionError, match="not authorized"):
        perception.capture_screen(
            CaptureAuthorization(
                explicit_request=False,
                contextually_required=False,
                reason="idle curiosity",
            )
        )

    assert adapter.captures == 0


def test_authorized_capture_is_ephemeral_and_typed() -> None:
    adapter = FakePerception()
    perception = PerceptionCoordinator(adapter)

    snapshot = perception.capture_screen(
        CaptureAuthorization(
            explicit_request=True,
            contextually_required=False,
            reason="Yuvraj asked what is on screen",
        )
    )

    assert snapshot.mime_type == "image/png"
    assert snapshot.sha256 == "b6a54bb9eb0fd8358087a5100cc9946e9b6d8420020e3c0abc8e30b0912363da"
    assert adapter.captures == 1


def test_active_window_and_health_do_not_capture_pixels() -> None:
    adapter = FakePerception()
    perception = PerceptionCoordinator(adapter)

    assert perception.active_window().process_name == "Code.exe"
    assert perception.system_health().cpu_percent == 12.5
    assert adapter.captures == 0

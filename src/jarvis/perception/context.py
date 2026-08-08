import hashlib
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class PerceptionValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActiveWindowSnapshot(PerceptionValue):
    title: str = Field(max_length=4_096)
    process_id: int = Field(ge=0)
    process_name: str = Field(min_length=1, max_length=512)
    executable_path: str | None = Field(default=None, max_length=32_768)
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _require_aware(value)


class ScreenSnapshot(PerceptionValue):
    png_bytes: bytes = Field(min_length=8, repr=False)
    width: int = Field(gt=0, le=32_768)
    height: int = Field(gt=0, le=32_768)
    captured_at: datetime
    source: Literal["active_window", "monitor", "virtual_desktop"]
    mime_type: Literal["image/png"] = "image/png"

    @field_validator("png_bytes")
    @classmethod
    def require_png_signature(cls, value: bytes) -> bytes:
        if not value.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("screen captures must be encoded as PNG")
        return value

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @computed_field
    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.png_bytes).hexdigest()


class SystemHealthSnapshot(PerceptionValue):
    cpu_percent: float = Field(ge=0, le=100)
    memory_percent: float = Field(ge=0, le=100)
    available_memory_bytes: int = Field(ge=0)
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _require_aware(value)


class CaptureAuthorization(PerceptionValue):
    explicit_request: bool
    contextually_required: bool
    reason: str = Field(min_length=1, max_length=500)


class PerceptionAdapter(Protocol):
    def active_window(self) -> ActiveWindowSnapshot: ...

    def capture_screen(self) -> ScreenSnapshot: ...

    def system_health(self) -> SystemHealthSnapshot: ...


class PerceptionCoordinator:
    def __init__(self, adapter: PerceptionAdapter) -> None:
        self._adapter = adapter

    def active_window(self) -> ActiveWindowSnapshot:
        return self._adapter.active_window()

    def capture_screen(self, authorization: CaptureAuthorization) -> ScreenSnapshot:
        if not authorization.explicit_request and not authorization.contextually_required:
            raise PermissionError("screen capture is not authorized")
        return self._adapter.capture_screen()

    def system_health(self) -> SystemHealthSnapshot:
        return self._adapter.system_health()


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("capture timestamps must include a UTC offset")
    return value

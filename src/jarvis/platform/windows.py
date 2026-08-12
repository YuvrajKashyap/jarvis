import ctypes
import json
from collections.abc import Callable
from ctypes import wintypes
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol

import psutil
from PIL import Image, ImageFilter, ImageGrab, ImageStat

from jarvis.agency.windows import WindowActionResult, WindowElement, WindowSnapshot
from jarvis.perception.context import (
    ActiveWindowSnapshot,
    ScreenSnapshot,
    SystemHealthSnapshot,
)
from jarvis.perception.placement import (
    DesktopLayout,
    PlacementRequest,
    Rect,
    placement_regions,
)
from jarvis.platform.process import BoundedProcessResult

MAX_AUTOMATION_ELEMENTS = 256


class ExecutableRunner(Protocol):
    async def run(
        self,
        executable: Path,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
        output_limit_bytes: int,
    ) -> BoundedProcessResult: ...


class WinAppAutomation:
    """Provides bounded UIA-pattern access to the foreground Windows application."""

    def __init__(
        self,
        *,
        executable: Path,
        runner: ExecutableRunner,
        window_handle: Callable[[], int],
        working_directory: Path,
    ) -> None:
        self._executable = executable
        self._runner = runner
        self._window_handle = window_handle
        self._working_directory = working_directory

    async def inspect_active(self, *, depth: int, interactive_only: bool) -> WindowSnapshot:
        if depth < 1 or depth > 6:
            raise ValueError("Windows inspection depth must be between one and six")
        handle = self._active_handle()
        arguments = [
            "ui",
            "inspect",
            "--window",
            str(handle),
            "--json",
            "--depth",
            str(depth),
        ]
        if interactive_only:
            arguments.append("--interactive")
        arguments.extend(("--hide-disabled", "--hide-offscreen"))
        payload = await self._execute(tuple(arguments))
        windows = payload.get("windows")
        if not isinstance(windows, list) or len(windows) != 1:
            raise RuntimeError("Windows UI Automation did not return the active window")
        window = windows[0]
        if not isinstance(window, dict) or int(window.get("hwnd", 0)) != handle:
            raise RuntimeError("Windows UI Automation returned a stale active window")
        raw_elements = window.get("elements", [])
        if not isinstance(raw_elements, list):
            raise RuntimeError("Windows UI Automation returned malformed elements")
        remaining = [MAX_AUTOMATION_ELEMENTS]
        elements = tuple(_parse_element(item, remaining) for item in raw_elements)
        return WindowSnapshot(
            window_handle=handle,
            title=str(window.get("title", "")),
            captured_at=datetime.now(UTC),
            elements=elements,
        )

    async def invoke_active(self, selector: str) -> WindowActionResult:
        return await self._act("invoke", selector)

    async def set_active_value(self, selector: str, value: str) -> WindowActionResult:
        _require_operand(value)
        return await self._act("set-value", selector, value)

    async def _act(
        self,
        command: str,
        selector: str,
        value: str | None = None,
    ) -> WindowActionResult:
        _require_operand(selector)
        handle = self._active_handle()
        arguments = ["ui", command, selector]
        if value is not None:
            arguments.append(value)
        arguments.extend(("--window", str(handle), "--json"))
        payload = await self._execute(tuple(arguments))
        if payload.get("success") is False:
            raise RuntimeError(str(payload.get("message", "Windows UI Automation action failed")))
        detail = payload.get("message", payload.get("detail", "Completed through UI Automation"))
        return WindowActionResult(
            window_handle=handle,
            operation="invoke" if command == "invoke" else "set_value",
            selector=selector,
            detail=str(detail)[:4_096],
            completed_at=datetime.now(UTC),
        )

    async def _execute(self, arguments: tuple[str, ...]) -> dict[str, object]:
        result = await self._runner.run(
            self._executable,
            arguments,
            cwd=self._working_directory,
            environment={"WINAPP_CLI_TELEMETRY_OPTOUT": "1"},
            timeout_seconds=10,
            output_limit_bytes=262_144,
        )
        if result.timed_out:
            raise RuntimeError("Windows UI Automation timed out")
        if result.truncated:
            raise RuntimeError("Windows UI Automation exceeded its output limit")
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout or "Windows UI Automation failed")[:4_096]
            raise RuntimeError(detail)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("Windows UI Automation returned malformed JSON") from error
        if not isinstance(payload, dict):
            raise RuntimeError("Windows UI Automation returned a non-object result")
        return payload

    def _active_handle(self) -> int:
        handle = self._window_handle()
        if handle <= 0:
            raise RuntimeError("there is no active Windows application")
        return handle


def foreground_window_handle() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow())


def _parse_element(raw: object, remaining: list[int]) -> WindowElement:
    if not isinstance(raw, dict):
        raise RuntimeError("Windows UI Automation returned a malformed element")
    if remaining[0] <= 0:
        raise RuntimeError("Windows UI Automation exceeded the element limit")
    remaining[0] -= 1
    raw_children = raw.get("children", [])
    if not isinstance(raw_children, list):
        raise RuntimeError("Windows UI Automation returned malformed child elements")
    children = tuple(_parse_element(child, remaining) for child in raw_children)
    selector = raw.get("selector")
    return WindowElement(
        selector=selector if isinstance(selector, str) else None,
        control_type=str(raw.get("type", "Unknown")),
        name=str(raw.get("name", "")),
        class_name=str(raw.get("className", "")),
        enabled=bool(raw.get("isEnabled", False)),
        offscreen=bool(raw.get("isOffscreen", False)),
        invokable=bool(raw.get("isInvokable", False)),
        children=children,
    )


def _require_operand(value: str) -> None:
    if not value or len(value) > 16_000:
        raise ValueError("Windows automation operand length is invalid")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError("Windows automation operands cannot contain control separators")


class WindowsPerception:
    def active_window(self) -> ActiveWindowSnapshot:
        user32 = ctypes.windll.user32
        window = user32.GetForegroundWindow()
        if not window:
            return ActiveWindowSnapshot(
                title="",
                process_id=0,
                process_name="unknown",
                executable_path=None,
                captured_at=datetime.now(UTC),
            )

        title_length = user32.GetWindowTextLengthW(window)
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(window, title_buffer, len(title_buffer))
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        name = "unknown"
        executable_path: str | None = None
        try:
            process = psutil.Process(process_id.value)
            name = process.name()
            executable_path = process.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            pass
        return ActiveWindowSnapshot(
            title=title_buffer.value,
            process_id=process_id.value,
            process_name=name,
            executable_path=executable_path,
            captured_at=datetime.now(UTC),
        )

    def capture_screen(self) -> ScreenSnapshot:
        image = ImageGrab.grab(all_screens=True, include_layered_windows=False)
        rgb_image = image.convert("RGB")
        encoded = BytesIO()
        rgb_image.save(encoded, format="PNG", optimize=False)
        return ScreenSnapshot(
            png_bytes=encoded.getvalue(),
            width=rgb_image.width,
            height=rgb_image.height,
            captured_at=datetime.now(UTC),
            source="virtual_desktop",
        )

    def system_health(self) -> SystemHealthSnapshot:
        memory = psutil.virtual_memory()
        return SystemHealthSnapshot(
            cpu_percent=psutil.cpu_percent(interval=None),
            memory_percent=memory.percent,
            available_memory_bytes=memory.available,
            captured_at=datetime.now(UTC),
        )


class WindowsDesktopLayoutProbe:
    """Builds a transient local occupancy map without encoding or retaining a screenshot."""

    def __init__(self, capture: Callable[[], Image.Image] | None = None) -> None:
        self._capture = capture or _capture_virtual_desktop

    def inspect(self, request: PlacementRequest) -> DesktopLayout:
        image = self._capture().convert("L")
        virtual_left = min(monitor.bounds.left for monitor in request.monitors)
        virtual_top = min(monitor.bounds.top for monitor in request.monitors)
        density = {
            (monitor_index, anchor_index): _visual_density(
                image,
                region,
                virtual_left=virtual_left,
                virtual_top=virtual_top,
            )
            for monitor_index, anchor_index, region in placement_regions(request)
        }
        current = Rect(
            left=request.overlay.left,
            top=request.overlay.top,
            width=request.overlay.width,
            height=request.overlay.height,
        )
        return DesktopLayout(
            region_density=density,
            current_density=_visual_density(
                image,
                current,
                virtual_left=virtual_left,
                virtual_top=virtual_top,
            ),
            attention=_attention_region(request),
        )


def _capture_virtual_desktop() -> Image.Image:
    return ImageGrab.grab(all_screens=True, include_layered_windows=False)


def _attention_region(request: PlacementRequest) -> Rect:
    monitor = next(
        (item for item in request.monitors if item.bounds.contains(request.pointer)),
        request.monitors[0],
    )
    width = min(720, monitor.work_area.width)
    height = min(520, monitor.work_area.height)
    left = round(request.pointer.x - width / 2)
    top = round(request.pointer.y - height / 2)
    return Rect(
        left=max(monitor.work_area.left, min(left, monitor.work_area.right - width)),
        top=max(monitor.work_area.top, min(top, monitor.work_area.bottom - height)),
        width=width,
        height=height,
    )


def _visual_density(
    image: Image.Image,
    region: Rect,
    *,
    virtual_left: int,
    virtual_top: int,
) -> float:
    left = max(0, region.left - virtual_left)
    top = max(0, region.top - virtual_top)
    right = min(image.width, region.right - virtual_left)
    bottom = min(image.height, region.bottom - virtual_top)
    if right <= left or bottom <= top:
        return 1.0
    sample = image.crop((left, top, right, bottom))
    sample.thumbnail((192, 128), Image.Resampling.BILINEAR)
    if sample.width < 3 or sample.height < 3:
        return 1.0
    edges = sample.filter(ImageFilter.FIND_EDGES).crop((1, 1, sample.width - 1, sample.height - 1))
    edge_mean = ImageStat.Stat(edges).mean[0] / 255
    contrast = min(ImageStat.Stat(sample).stddev[0] / 72, 1)
    return min(edge_mean * 2.8 + contrast * 0.35, 1)

import ctypes
from ctypes import wintypes
from datetime import UTC, datetime
from io import BytesIO

import psutil
from PIL import ImageGrab

from jarvis.perception.context import (
    ActiveWindowSnapshot,
    ScreenSnapshot,
    SystemHealthSnapshot,
)


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

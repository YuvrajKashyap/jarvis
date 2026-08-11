import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path("scripts/desktop_acceptance.py")
SPEC = importlib.util.spec_from_file_location("desktop_acceptance", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
desktop_acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desktop_acceptance)
WINDOWS_GUI_SUBSYSTEM = desktop_acceptance.WINDOWS_GUI_SUBSYSTEM
pe_subsystem = desktop_acceptance.pe_subsystem


def test_packaged_host_uses_the_windows_gui_subsystem() -> None:
    image = bytearray(512)
    image[0:2] = b"MZ"
    image[0x3C:0x40] = (0x80).to_bytes(4, "little")
    image[0x80:0x84] = b"PE\0\0"
    optional_header = 0x80 + 4 + 20
    image[optional_header : optional_header + 2] = (0x20B).to_bytes(2, "little")
    image[optional_header + 68 : optional_header + 70] = WINDOWS_GUI_SUBSYSTEM.to_bytes(2, "little")

    assert pe_subsystem(bytes(image)) == WINDOWS_GUI_SUBSYSTEM


def test_packaged_host_rejects_a_console_subsystem() -> None:
    image = bytearray(512)
    image[0:2] = b"MZ"
    image[0x3C:0x40] = (0x80).to_bytes(4, "little")
    image[0x80:0x84] = b"PE\0\0"
    optional_header = 0x80 + 4 + 20
    image[optional_header : optional_header + 2] = (0x20B).to_bytes(2, "little")
    image[optional_header + 68 : optional_header + 70] = (3).to_bytes(2, "little")

    assert pe_subsystem(bytes(image)) != WINDOWS_GUI_SUBSYSTEM


def test_packaged_webview_csp_allows_only_the_local_tauri_ipc_endpoint() -> None:
    config = json.loads(Path("src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    csp = config["app"]["security"]["csp"]

    assert "connect-src 'self' http://ipc.localhost" in csp


def test_packaged_overlay_composites_over_the_desktop_without_an_opaque_host_surface() -> None:
    config = json.loads(Path("src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    overlay = next(window for window in config["app"]["windows"] if window["label"] == "overlay")

    assert overlay["transparent"] is True


def test_packaged_overlay_supports_native_edge_and_corner_resizing() -> None:
    config = json.loads(Path("src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    overlay = next(window for window in config["app"]["windows"] if window["label"] == "overlay")

    assert overlay["resizable"] is True
    assert overlay["minWidth"] >= 360
    # The fixed header, pairing control, and 44px composer must retain their
    # translucent surface inset even when the transcript collapses.
    assert overlay["minHeight"] >= 224
    assert overlay["minHeight"] <= overlay["height"] <= 280

from __future__ import annotations

import argparse
import json
from pathlib import Path

WINDOWS_GUI_SUBSYSTEM = 2
_PE_SIGNATURE = b"PE\0\0"
_PE32_MAGIC = 0x10B
_PE32_PLUS_MAGIC = 0x20B


def pe_subsystem(image: bytes) -> int:
    if len(image) < 64 or image[:2] != b"MZ":
        raise ValueError("file is not a Windows PE image")
    pe_offset = int.from_bytes(image[0x3C:0x40], "little")
    optional_header = pe_offset + len(_PE_SIGNATURE) + 20
    if pe_offset < 64 or optional_header + 70 > len(image):
        raise ValueError("Windows PE headers are truncated")
    if image[pe_offset : pe_offset + len(_PE_SIGNATURE)] != _PE_SIGNATURE:
        raise ValueError("Windows PE signature is missing")
    magic = int.from_bytes(image[optional_header : optional_header + 2], "little")
    if magic not in {_PE32_MAGIC, _PE32_PLUS_MAGIC}:
        raise ValueError("Windows PE optional header is unsupported")
    return int.from_bytes(image[optional_header + 68 : optional_header + 70], "little")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate packaged JARVIS desktop invariants.")
    parser.add_argument("host", type=Path, help="Path to the packaged jarvis-host.exe")
    args = parser.parse_args()
    subsystem = pe_subsystem(args.host.read_bytes())
    if subsystem != WINDOWS_GUI_SUBSYSTEM:
        raise SystemExit(
            f"jarvis-host.exe uses PE subsystem {subsystem}; expected Windows GUI subsystem 2"
        )
    print(json.dumps({"host": str(args.host), "windows_gui_subsystem": True}))


if __name__ == "__main__":
    main()

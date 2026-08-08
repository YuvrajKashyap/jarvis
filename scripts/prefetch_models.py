from __future__ import annotations

import argparse
import os
from pathlib import Path


def default_model_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / "JARVIS" / "models"


def prefetch_core_models(model_directory: Path) -> None:
    from faster_whisper.utils import download_model
    from openwakeword.utils import download_models

    wake_directory = model_directory / "openwakeword"
    whisper_directory = model_directory / "faster-whisper" / "distil-small.en"
    wake_directory.mkdir(parents=True, exist_ok=True)
    whisper_directory.mkdir(parents=True, exist_ok=True)
    download_models(model_names=["hey_jarvis"], target_directory=str(wake_directory))
    download_model("distil-small.en", output_dir=str(whisper_directory))


def main() -> None:
    parser = argparse.ArgumentParser(description="Download JARVIS local model assets")
    parser.add_argument("--core", action="store_true", help="download wake and speech models")
    parser.add_argument(
        "--directory",
        type=Path,
        default=default_model_directory(),
        help="managed model directory",
    )
    arguments = parser.parse_args()
    if not arguments.core:
        parser.error("select at least one model group")
    prefetch_core_models(arguments.directory.resolve())


if __name__ == "__main__":
    main()

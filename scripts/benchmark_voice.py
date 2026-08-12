from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import wave
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from jarvis.platform.resources import WindowsResourceProbe
from jarvis.platform.voice import ChatterboxTurboSynthesizer
from jarvis.speech.output import SpeechSynthesizer, SynthesizedAudio

DEFAULT_PROMPT = "Good evening, Yuvraj. The build is green."
MINIMUM_AVAILABLE_MEMORY_BYTES = 3 * 1024**3


class VoiceMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    synthesis_seconds: float = Field(ge=0)
    audio_seconds: float = Field(gt=0)
    realtime_factor: float = Field(ge=0)
    faster_than_realtime: bool
    sample_rate: int = Field(ge=8_000, le=96_000)


async def measure_voice(
    synthesizer: SpeechSynthesizer,
    *,
    model: str,
    prompt: str,
) -> tuple[VoiceMeasurement, SynthesizedAudio]:
    started = time.perf_counter()
    audio = await synthesizer.synthesize(prompt)
    elapsed = time.perf_counter() - started
    audio_seconds = len(audio.pcm_s16le) / (audio.sample_rate * 2)
    realtime_factor = elapsed / audio_seconds
    return (
        VoiceMeasurement(
            model=model,
            synthesis_seconds=elapsed,
            audio_seconds=audio_seconds,
            realtime_factor=realtime_factor,
            faster_than_realtime=realtime_factor < 1,
            sample_rate=audio.sample_rate,
        ),
        audio,
    )


async def run(reference: Path, *, device: str, prompt: str) -> tuple[VoiceMeasurement, ...]:
    snapshot = WindowsResourceProbe().snapshot()
    if snapshot.available_memory_bytes < MINIMUM_AVAILABLE_MEMORY_BYTES:
        raise RuntimeError(
            "voice benchmark refused: at least 3 GiB of available memory is required before load"
        )
    output_directory = _data_directory() / "voice-candidates"
    output_directory.mkdir(parents=True, exist_ok=True)
    measurements: list[VoiceMeasurement] = []
    for name, nano in (("nano", True), ("turbo", False)):
        synthesizer = ChatterboxTurboSynthesizer(
            reference_path=reference,
            device=device,
            nano=nano,
        )
        measurement, audio = await measure_voice(synthesizer, model=name, prompt=prompt)
        _write_wav(output_directory / f"{name}.wav", audio)
        measurements.append(measurement)
    report = output_directory / "benchmark.json"
    report.write_text(
        json.dumps([item.model_dump(mode="json") for item in measurements], indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(report)
    return tuple(measurements)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark private local JARVIS voice candidates")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--confirm-original-reference", action="store_true")
    arguments = parser.parse_args()
    if not arguments.confirm_original_reference:
        raise SystemExit("confirm that the supplied voice reference is lawful and original")
    asyncio.run(run(arguments.reference, device=arguments.device, prompt=arguments.prompt))


def _write_wav(path: Path, audio: SynthesizedAudio) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(audio.sample_rate)
        output.writeframes(audio.pcm_s16le)


def _data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / "JARVIS"


if __name__ == "__main__":
    main()

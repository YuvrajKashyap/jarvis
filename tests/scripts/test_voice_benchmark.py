import importlib.util
from pathlib import Path

import pytest

from jarvis.speech.output import SynthesizedAudio

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_voice_benchmark",
    Path(__file__).resolve().parents[2] / "scripts" / "benchmark_voice.py",
)
assert _SPEC is not None and _SPEC.loader is not None
voice_benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(voice_benchmark)


class Synthesizer:
    async def synthesize(self, text: str) -> SynthesizedAudio:
        assert text == "Good evening, Yuvraj. The build is green."
        return SynthesizedAudio(sample_rate=16_000, pcm_s16le=b"\x00\x00" * 32_000)


@pytest.mark.asyncio
async def test_voice_measurement_reports_audio_duration_and_realtime_factor(monkeypatch) -> None:
    times = iter((10.0, 10.5))
    monkeypatch.setattr(voice_benchmark.time, "perf_counter", lambda: next(times))

    measurement, audio = await voice_benchmark.measure_voice(
        Synthesizer(),
        model="nano",
        prompt="Good evening, Yuvraj. The build is green.",
    )

    assert measurement.audio_seconds == 2
    assert measurement.synthesis_seconds == 0.5
    assert measurement.realtime_factor == 0.25
    assert measurement.faster_than_realtime is True
    assert audio.sample_rate == 16_000

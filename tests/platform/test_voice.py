from pathlib import Path

import numpy as np
import pytest

from jarvis.platform.voice import ChatterboxTurboSynthesizer, SoundDeviceSpeaker
from jarvis.speech.output import SynthesizedAudio


class FakeChatterbox:
    sr = 24_000

    def __init__(self) -> None:
        self.reference: str | None = None
        self.requests: list[str] = []

    def prepare_conditionals(self, reference: str) -> None:
        self.reference = reference

    def generate(self, text: str) -> np.ndarray:
        self.requests.append(text)
        return np.array([[-1.5, -0.5, 0.5, 1.5]], dtype=np.float32)


@pytest.mark.asyncio
async def test_chatterbox_is_lazy_conditions_once_and_emits_bounded_pcm(tmp_path: Path) -> None:
    reference = tmp_path / "jarvis-reference.wav"
    reference.write_bytes(b"private-original-reference")
    model = FakeChatterbox()
    loads: list[str] = []

    def load(device: str) -> FakeChatterbox:
        loads.append(device)
        return model

    voice = ChatterboxTurboSynthesizer(
        reference_path=reference,
        device="cpu",
        loader=load,
    )
    first = await voice.synthesize("Good evening, Yuvraj.")
    second = await voice.synthesize("The build is green.")

    assert loads == ["cpu"]
    assert model.reference == str(reference)
    assert model.requests == ["Good evening, Yuvraj.", "The build is green."]
    assert first.sample_rate == second.sample_rate == 24_000
    samples = np.frombuffer(first.pcm_s16le, dtype="<i2")
    assert samples.tolist() == [-32767, -16383, 16383, 32767]


def test_chatterbox_requires_a_private_reference_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reference voice"):
        ChatterboxTurboSynthesizer(reference_path=tmp_path / "missing.wav")


def test_chatterbox_rejects_unknown_devices(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"voice")
    with pytest.raises(ValueError, match="cpu or cuda"):
        ChatterboxTurboSynthesizer(reference_path=reference, device="internet")


@pytest.mark.asyncio
async def test_chatterbox_rejects_empty_and_unbounded_text(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"voice")
    voice = ChatterboxTurboSynthesizer(
        reference_path=reference,
        loader=lambda _device: FakeChatterbox(),
    )

    with pytest.raises(ValueError, match="cannot be empty"):
        await voice.synthesize("  ")
    with pytest.raises(ValueError, match="4000"):
        await voice.synthesize("x" * 4_001)


class FakeSoundDevice:
    def __init__(self) -> None:
        self.played: list[tuple[np.ndarray, int, bool, int | str | None]] = []
        self.stopped = 0

    def play(
        self,
        data: np.ndarray,
        samplerate: int,
        *,
        blocking: bool,
        device: int | str | None = None,
    ) -> None:
        self.played.append((data, samplerate, blocking, device))

    def stop(self) -> None:
        self.stopped += 1


@pytest.mark.asyncio
async def test_desktop_speaker_plays_and_cancels_through_sounddevice() -> None:
    backend = FakeSoundDevice()
    speaker = SoundDeviceSpeaker(backend=backend, device="Headphones")

    await speaker.play(SynthesizedAudio(sample_rate=24_000, pcm_s16le=b"\xff\x7f\x00\x80"))
    await speaker.cancel()

    samples, sample_rate, blocking, device = backend.played[0]
    assert sample_rate == 24_000
    assert blocking is True
    assert device == "Headphones"
    assert samples.dtype == np.float32
    assert samples.tolist() == pytest.approx([32767 / 32768, -1.0])
    assert backend.stopped == 1

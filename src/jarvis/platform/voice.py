import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import numpy as np
from numpy.typing import NDArray

from jarvis.speech.output import SynthesizedAudio


class ChatterboxModel(Protocol):
    sr: int

    def prepare_conditionals(self, reference: str) -> None: ...

    def generate(self, text: str) -> object: ...


class TensorLike(Protocol):
    def detach(self) -> "TensorLike": ...

    def cpu(self) -> "TensorLike": ...

    def numpy(self) -> NDArray[np.floating]: ...


class SoundDeviceBackend(Protocol):
    def play(
        self,
        data: NDArray[np.float32],
        samplerate: int,
        *,
        blocking: bool,
    ) -> None: ...

    def stop(self) -> None: ...


class SoundDeviceSpeaker:
    def __init__(self, *, backend: SoundDeviceBackend | None = None) -> None:
        self._configured_backend = backend
        self._loaded_backend: SoundDeviceBackend | None = None

    async def play(self, audio: SynthesizedAudio) -> None:
        await asyncio.to_thread(self._play, audio)

    async def cancel(self) -> None:
        await asyncio.to_thread(self.cancel_now)

    def cancel_now(self) -> None:
        self._backend().stop()

    def _play(self, audio: SynthesizedAudio) -> None:
        samples = np.frombuffer(audio.pcm_s16le, dtype="<i2").astype(np.float32) / 32_768.0
        self._backend().play(samples, audio.sample_rate, blocking=True)

    def _backend(self) -> SoundDeviceBackend:
        if self._configured_backend is not None:
            return self._configured_backend
        if self._loaded_backend is None:
            import sounddevice

            self._loaded_backend = cast(SoundDeviceBackend, sounddevice)
        return self._loaded_backend


class ChatterboxTurboSynthesizer:
    """Lazy local voice-cloning adapter; no model or reference leaves the machine."""

    def __init__(
        self,
        *,
        reference_path: Path,
        device: str = "cuda",
        loader: Callable[[str], ChatterboxModel] | None = None,
    ) -> None:
        reference = reference_path.resolve()
        if not reference.is_file():
            raise ValueError("the selected private reference voice file does not exist")
        if device not in {"cpu", "cuda"}:
            raise ValueError("Chatterbox device must be cpu or cuda")
        self._reference_path = reference
        self._device = device
        self._loader = loader or _load_chatterbox
        self._model: ChatterboxModel | None = None
        self._lock = threading.Lock()

    async def synthesize(self, text: str) -> SynthesizedAudio:
        normalized = text.strip()
        if not normalized:
            raise ValueError("speech text cannot be empty")
        if len(normalized) > 4_000:
            raise ValueError("speech text cannot exceed 4000 characters")
        return await asyncio.to_thread(self._synthesize, normalized)

    def _synthesize(self, text: str) -> SynthesizedAudio:
        with self._lock:
            model = self._model
            if model is None:
                model = self._loader(self._device)
                model.prepare_conditionals(str(self._reference_path))
                self._model = model
            waveform = _as_float_waveform(model.generate(text))
            if waveform.size == 0:
                raise RuntimeError("voice model returned empty audio")
            pcm = (np.clip(waveform, -1.0, 1.0) * 32_767).astype("<i2").tobytes()
            return SynthesizedAudio(sample_rate=int(model.sr), pcm_s16le=pcm)


def _load_chatterbox(device: str) -> ChatterboxModel:
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    return cast(ChatterboxModel, ChatterboxTurboTTS.from_pretrained(device=device))


def _as_float_waveform(value: object) -> NDArray[np.float32]:
    if isinstance(value, np.ndarray):
        array = value
    else:
        tensor = cast(TensorLike, value)
        array = tensor.detach().cpu().numpy()
    return np.asarray(array, dtype=np.float32).reshape(-1)

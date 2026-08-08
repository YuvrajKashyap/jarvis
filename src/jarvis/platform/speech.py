import asyncio
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray


class ScalarValue(Protocol):
    def item(self) -> float: ...


class VadModel(Protocol):
    def __call__(self, audio: object, sample_rate: int) -> ScalarValue: ...


class WakeModel(Protocol):
    def predict(self, audio: NDArray[np.int16]) -> dict[str, float]: ...


class Segment(Protocol):
    text: str


class WhisperModel(Protocol):
    def transcribe(
        self,
        audio: NDArray[np.float32],
        **kwargs: object,
    ) -> tuple[Iterable[Segment], object]: ...


class WhisperFactory(Protocol):
    def __call__(
        self,
        model_name: str,
        *,
        device: str,
        compute_type: str,
        cpu_threads: int,
        num_workers: int,
    ) -> WhisperModel: ...


class RawInputStream(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


class RawInputStreamFactory(Protocol):
    def __call__(self, **kwargs: object) -> RawInputStream: ...


class SoundDeviceMicrophone:
    def __init__(
        self,
        *,
        stream_factory: RawInputStreamFactory | None = None,
        device: int | str | None = None,
    ) -> None:
        self._stream_factory = stream_factory or _sounddevice_stream_factory
        self._device = device
        self._stream: RawInputStream | None = None
        self._lock = threading.RLock()

    def start(self, on_frame: Callable[[bytes], None]) -> None:
        with self._lock:
            if self._stream is not None:
                return

            def callback(
                indata: object,
                frames: int,
                _time_info: object,
                _status: object,
            ) -> None:
                pcm = memoryview(cast(Any, indata)).tobytes()
                if frames == 512 and len(pcm) == 1_024:
                    on_frame(pcm)

            stream = self._stream_factory(
                samplerate=16_000,
                blocksize=512,
                dtype="int16",
                channels=1,
                latency="low",
                device=self._device,
                callback=callback,
            )
            stream.start()
            self._stream = stream

    def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            stream.stop()
            stream.close()


class SileroVad:
    def __init__(self, *, model: VadModel | None = None, threshold: float = 0.5) -> None:
        if not 0 < threshold < 1:
            raise ValueError("VAD threshold must be between zero and one")
        self._model = model
        self._threshold = threshold
        self._lock = threading.RLock()

    def is_speech(self, pcm: bytes) -> bool:
        if len(pcm) != 512 * 2:
            raise ValueError("Silero VAD requires exactly 512 mono int16 samples")
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32_768.0
        with self._lock:
            model = self._model or _load_silero_model()
            self._model = model
            return float(model(audio, 16_000).item()) >= self._threshold


class OpenWakeWordDetector:
    def __init__(
        self,
        *,
        model: WakeModel | None = None,
        model_path: Path | None = None,
        threshold: float = 0.5,
    ) -> None:
        if not 0 < threshold < 1:
            raise ValueError("wake-word threshold must be between zero and one")
        self._model = model
        self._model_path = model_path
        self._threshold = threshold
        self._lock = threading.RLock()

    def detect(self, pcm: bytes) -> bool:
        if not pcm or len(pcm) % 2:
            raise ValueError("wake-word PCM must contain aligned int16 samples")
        audio = np.frombuffer(pcm, dtype="<i2")
        with self._lock:
            model = self._model or _load_wake_model(self._model_path)
            self._model = model
            scores = model.predict(audio)
        return float(scores.get("hey_jarvis", 0)) >= self._threshold


class FasterWhisperTranscriber:
    def __init__(
        self,
        *,
        model_factory: WhisperFactory | None = None,
        model_name: str = "distil-small.en",
        cpu_threads: int = 4,
    ) -> None:
        if not model_name or len(model_name) > 512:
            raise ValueError("Whisper model name is invalid")
        if cpu_threads < 1 or cpu_threads > 16:
            raise ValueError("Whisper CPU thread count is invalid")
        self._factory = model_factory or _whisper_factory
        self._model_name = model_name
        self._cpu_threads = cpu_threads
        self._model: WhisperModel | None = None
        self._lock = threading.RLock()

    async def transcribe(self, pcm: bytes) -> str:
        if not pcm or len(pcm) % 2:
            raise ValueError("transcription PCM must contain aligned int16 samples")
        return await asyncio.to_thread(self._transcribe, pcm)

    def _transcribe(self, pcm: bytes) -> str:
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32_768.0
        with self._lock:
            if self._model is None:
                self._model = self._factory(
                    self._model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=self._cpu_threads,
                    num_workers=1,
                )
            segments, _information = self._model.transcribe(
                audio,
                language="en",
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=False,
                without_timestamps=True,
            )
            return " ".join(segment.text.strip() for segment in segments if segment.text.strip())


def _load_silero_model() -> VadModel:
    import torch
    from silero_vad import load_silero_vad

    model = load_silero_vad(onnx=True)

    def predict(audio: object, sample_rate: int) -> ScalarValue:
        array = cast(NDArray[np.float32], audio)
        return cast(ScalarValue, model(torch.from_numpy(array), sample_rate))

    return predict


def _load_wake_model(model_path: Path | None) -> WakeModel:
    import openwakeword
    from openwakeword.model import Model

    resolved_path = model_path
    if resolved_path is None:
        bundled_path = Path(openwakeword.MODELS["hey_jarvis"]["model_path"])
        resolved_path = bundled_path.with_suffix(".onnx")
    return cast(
        WakeModel,
        Model(
            wakeword_models=[str(resolved_path)],
            inference_framework="onnx",
            vad_threshold=0,
            melspec_model_path=str(resolved_path.parent / "melspectrogram.onnx"),
            embedding_model_path=str(resolved_path.parent / "embedding_model.onnx"),
        ),
    )


def _whisper_factory(
    model_name: str,
    *,
    device: str,
    compute_type: str,
    cpu_threads: int,
    num_workers: int,
) -> WhisperModel:
    from faster_whisper import WhisperModel as FasterWhisperModel

    return cast(
        WhisperModel,
        FasterWhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            num_workers=num_workers,
        ),
    )


def _sounddevice_stream_factory(**kwargs: object) -> RawInputStream:
    import sounddevice

    return cast(RawInputStream, sounddevice.RawInputStream(**kwargs))

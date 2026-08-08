from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import numpy as np

from jarvis.platform.speech import (
    FasterWhisperTranscriber,
    OpenWakeWordDetector,
    SileroVad,
    SoundDeviceMicrophone,
)


class Scalar:
    def __init__(self, value: float) -> None:
        self.value = value

    def item(self) -> float:
        return self.value


class FakeVadModel:
    def __init__(self, score: float) -> None:
        self.score = score
        self.samples = 0

    def __call__(self, audio: object, sample_rate: int) -> Scalar:
        array = cast(np.ndarray, audio)
        self.samples = int(array.shape[-1])
        assert sample_rate == 16_000
        return Scalar(self.score)


def test_silero_vad_normalizes_exact_pcm_window() -> None:
    model = FakeVadModel(0.72)
    vad = SileroVad(model=model, threshold=0.6)

    assert vad.is_speech((np.ones(512, dtype=np.int16) * 8_000).tobytes()) is True
    assert model.samples == 512


class FakeWakeModel:
    def __init__(self) -> None:
        self.seen_dtype: object = None

    def predict(self, audio: np.ndarray) -> dict[str, float]:
        self.seen_dtype = audio.dtype
        return {"hey_jarvis": 0.81}


def test_openwakeword_uses_local_jarvis_score_threshold() -> None:
    model = FakeWakeModel()
    detector = OpenWakeWordDetector(model=model, threshold=0.7)

    assert detector.detect(b"\x00\x00" * 1_280) is True
    assert model.seen_dtype == np.dtype("int16")


async def test_faster_whisper_runs_cpu_int8_and_joins_segments() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeWhisper:
        def transcribe(self, audio: np.ndarray, **kwargs: object):
            assert audio.dtype == np.float32
            assert kwargs["language"] == "en"
            return iter(
                [SimpleNamespace(text=" What am I"), SimpleNamespace(text=" looking at? ")]
            ), None

    def factory(
        model_name: str,
        *,
        device: str,
        compute_type: str,
        cpu_threads: int,
        num_workers: int,
    ) -> FakeWhisper:
        calls.append(
            (
                model_name,
                {
                    "device": device,
                    "compute_type": compute_type,
                    "cpu_threads": cpu_threads,
                    "num_workers": num_workers,
                },
            )
        )
        return FakeWhisper()

    transcriber = FasterWhisperTranscriber(model_factory=factory, model_name="distil-small.en")

    result = await transcriber.transcribe(b"\x01\x00" * 16_000)

    assert result == "What am I looking at?"
    assert calls == [
        (
            "distil-small.en",
            {"device": "cpu", "compute_type": "int8", "cpu_threads": 4, "num_workers": 1},
        )
    ]


def test_sounddevice_microphone_emits_only_complete_512_sample_frames() -> None:
    streams: list[object] = []

    class FakeStream:
        def __init__(self, **kwargs: object) -> None:
            self.callback = kwargs["callback"]
            self.started = False
            self.stopped = False
            self.closed = False
            streams.append(self)

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def close(self) -> None:
            self.closed = True

    frames: list[bytes] = []
    microphone = SoundDeviceMicrophone(stream_factory=FakeStream)
    microphone.start(frames.append)
    stream = cast(FakeStream, streams[0])
    callback = cast(Callable[[object, int, object, object], None], stream.callback)
    callback(memoryview(b"\x01\x00" * 512), 512, None, None)
    callback(memoryview(b"\x01\x00" * 100), 100, None, None)
    microphone.stop()

    assert frames == [b"\x01\x00" * 512]
    assert stream.started and stream.stopped and stream.closed

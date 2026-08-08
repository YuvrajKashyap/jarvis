import asyncio
from collections.abc import Callable

from jarvis.speech.audio import AudioFormat, AudioRingBuffer
from jarvis.speech.desktop import DesktopSpeechService, DesktopTranscript, DesktopWake
from jarvis.speech.engine import SpeechCoordinator, SpeechSettings

FORMAT = AudioFormat(sample_rate=16_000, channels=1, sample_width_bytes=2)
FRAME = b"\x00\x00" * 512


class FakeMicrophone:
    def __init__(self) -> None:
        self.on_frame: Callable[[bytes], None] | None = None
        self.stopped = False

    def start(self, on_frame: Callable[[bytes], None]) -> None:
        self.on_frame = on_frame

    def push(self, pcm: bytes) -> None:
        assert self.on_frame is not None
        self.on_frame(pcm)

    def stop(self) -> None:
        self.stopped = True


class FakeWake:
    should_wake = True

    def detect(self, pcm: bytes) -> bool:
        detected = self.should_wake
        self.should_wake = False
        return detected


class FakeVad:
    def is_speech(self, pcm: bytes) -> bool:
        return any(pcm)


class FakePlayback:
    def cancel(self) -> None:
        return None


class FakeTranscriber:
    async def transcribe(self, pcm: bytes) -> str:
        assert any(pcm)
        return "What am I looking at?"


async def test_desktop_service_turns_microphone_frames_into_wake_and_transcript() -> None:
    microphone = FakeMicrophone()
    coordinator = SpeechCoordinator(
        buffer=AudioRingBuffer(audio_format=FORMAT, duration_seconds=120),
        wake_word=FakeWake(),
        vad=FakeVad(),
        playback=FakePlayback(),
        settings=SpeechSettings(frame_duration_ms=32, end_of_speech_silence_ms=64),
    )
    service = DesktopSpeechService(
        microphone=microphone,
        coordinator=coordinator,
        transcriber=FakeTranscriber(),
    )
    await service.start()

    microphone.push(FRAME)
    wake = await asyncio.wait_for(service.next_event(), timeout=1)
    microphone.push(b"\x10\x00" * 512)
    microphone.push(FRAME)
    microphone.push(FRAME)
    transcript = await asyncio.wait_for(service.next_event(), timeout=1)
    await service.stop()

    assert isinstance(wake, DesktopWake)
    assert isinstance(transcript, DesktopTranscript)
    assert transcript.text == "What am I looking at?"
    assert microphone.stopped is True

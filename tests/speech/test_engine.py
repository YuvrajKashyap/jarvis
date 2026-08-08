from jarvis.speech.audio import AudioFormat, AudioRingBuffer
from jarvis.speech.engine import (
    BargeIn,
    SpeechCoordinator,
    SpeechPhase,
    SpeechSettings,
    UtteranceReady,
    WakeDetected,
)

FORMAT = AudioFormat(sample_rate=16_000, channels=1, sample_width_bytes=2)
FRAME_BYTES = 640


class FakeWakeWord:
    def __init__(self) -> None:
        self.should_wake = False

    def detect(self, pcm: bytes) -> bool:
        return self.should_wake


class FakeVad:
    def is_speech(self, pcm: bytes) -> bool:
        return any(pcm)


class FakePlayback:
    def __init__(self) -> None:
        self.cancelled = 0

    def cancel(self) -> None:
        self.cancelled += 1


def engine() -> tuple[SpeechCoordinator, FakeWakeWord, FakePlayback]:
    wake = FakeWakeWord()
    playback = FakePlayback()
    coordinator = SpeechCoordinator(
        buffer=AudioRingBuffer(audio_format=FORMAT, duration_seconds=120),
        wake_word=wake,
        vad=FakeVad(),
        playback=playback,
        settings=SpeechSettings(
            frame_duration_ms=20,
            end_of_speech_silence_ms=40,
            maximum_utterance_seconds=30,
        ),
    )
    return coordinator, wake, playback


def test_wake_detection_returns_ram_only_preroll_and_enters_listening() -> None:
    coordinator, wake, _playback = engine()
    coordinator.ingest(b"\x01" * FRAME_BYTES)
    wake.should_wake = True

    event = coordinator.ingest(b"\x02" * FRAME_BYTES)

    assert isinstance(event, WakeDetected)
    assert event.pre_roll_pcm.endswith(b"\x02" * FRAME_BYTES)
    assert coordinator.phase is SpeechPhase.LISTENING


def test_private_mode_zeros_preroll_but_keeps_wake_detection() -> None:
    coordinator, wake, _playback = engine()
    coordinator.set_private(True)
    wake.should_wake = True

    event = coordinator.ingest(b"\x02" * FRAME_BYTES)

    assert isinstance(event, WakeDetected)
    assert event.pre_roll_pcm == b""


def test_speech_followed_by_bounded_silence_finishes_utterance() -> None:
    coordinator, wake, _playback = engine()
    wake.should_wake = True
    coordinator.ingest(b"\x01" * FRAME_BYTES)
    wake.should_wake = False

    assert coordinator.ingest(b"\x03" * FRAME_BYTES) is None
    assert coordinator.ingest(b"\x00" * FRAME_BYTES) is None
    event = coordinator.ingest(b"\x00" * FRAME_BYTES)

    assert isinstance(event, UtteranceReady)
    assert event.pcm.startswith(b"\x03" * FRAME_BYTES)
    assert coordinator.phase is SpeechPhase.TRANSCRIBING


def test_voice_during_playback_cancels_audio_and_signals_barge_in() -> None:
    coordinator, _wake, playback = engine()
    coordinator.begin_playback()

    event = coordinator.ingest(b"\x04" * FRAME_BYTES)

    assert isinstance(event, BargeIn)
    assert playback.cancelled == 1
    assert coordinator.phase is SpeechPhase.LISTENING


def test_maximum_utterance_bounds_audio_growth() -> None:
    coordinator, wake, _playback = engine()
    wake.should_wake = True
    coordinator.ingest(b"\x01" * FRAME_BYTES)
    wake.should_wake = False

    event = None
    for _index in range(1_500):
        event = coordinator.ingest(b"\x05" * FRAME_BYTES)

    assert isinstance(event, UtteranceReady)
    assert len(event.pcm) <= FORMAT.bytes_per_second * 30

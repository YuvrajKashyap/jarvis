from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from jarvis.speech.audio import AudioRingBuffer


class WakeWordDetector(Protocol):
    def detect(self, pcm: bytes) -> bool: ...


class VoiceActivityDetector(Protocol):
    def is_speech(self, pcm: bytes) -> bool: ...


class AudioPlayback(Protocol):
    def cancel(self) -> None: ...


class SpeechSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    frame_duration_ms: int = Field(default=20, ge=10, le=100)
    end_of_speech_silence_ms: int = Field(default=600, ge=20, le=3_000)
    maximum_utterance_seconds: int = Field(default=30, ge=1, le=300)


class SpeechPhase(StrEnum):
    IDLE = "idle"
    PRIVATE = "private"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    PLAYING = "playing"


@dataclass(frozen=True)
class WakeDetected:
    pre_roll_pcm: bytes


@dataclass(frozen=True)
class UtteranceReady:
    pcm: bytes


@dataclass(frozen=True)
class BargeIn:
    captured_pcm: bytes


SpeechEvent = WakeDetected | UtteranceReady | BargeIn


class SpeechCoordinator:
    def __init__(
        self,
        *,
        buffer: AudioRingBuffer,
        wake_word: WakeWordDetector,
        vad: VoiceActivityDetector,
        playback: AudioPlayback,
        settings: SpeechSettings,
    ) -> None:
        self._buffer = buffer
        self._wake_word = wake_word
        self._vad = vad
        self._playback = playback
        self._settings = settings
        self._private = False
        self._utterance = bytearray()
        self._speech_started = False
        self._silence_ms = 0
        self.phase = SpeechPhase.IDLE

    def ingest(self, pcm: bytes) -> SpeechEvent | None:
        self._buffer.append(pcm)
        if self.phase is SpeechPhase.PLAYING and self._vad.is_speech(pcm):
            self._playback.cancel()
            self._reset_utterance()
            self._append_utterance(pcm)
            self._speech_started = True
            self.phase = SpeechPhase.LISTENING
            return BargeIn(captured_pcm=pcm)

        if self.phase in {SpeechPhase.IDLE, SpeechPhase.PRIVATE}:
            if self._wake_word.detect(pcm):
                self._reset_utterance()
                self.phase = SpeechPhase.LISTENING
                return WakeDetected(pre_roll_pcm=self._buffer.snapshot())
            return None

        if self.phase is not SpeechPhase.LISTENING:
            return None

        self._append_utterance(pcm)
        if self._vad.is_speech(pcm):
            self._speech_started = True
            self._silence_ms = 0
        elif self._speech_started:
            self._silence_ms += self._settings.frame_duration_ms

        maximum_bytes = (
            self._buffer.audio_format.bytes_per_second * self._settings.maximum_utterance_seconds
        )
        reached_silence = (
            self._speech_started and self._silence_ms >= self._settings.end_of_speech_silence_ms
        )
        if reached_silence or len(self._utterance) >= maximum_bytes:
            pcm_result = bytes(self._utterance[:maximum_bytes])
            self._reset_utterance()
            self.phase = SpeechPhase.TRANSCRIBING
            return UtteranceReady(pcm=pcm_result)
        return None

    def set_private(self, enabled: bool) -> None:
        self._private = enabled
        self._buffer.set_private(enabled)
        self._reset_utterance()
        self.phase = SpeechPhase.PRIVATE if enabled else SpeechPhase.IDLE

    def complete_transcription(self) -> None:
        if self.phase is not SpeechPhase.TRANSCRIBING:
            raise RuntimeError("speech engine is not transcribing")
        self.phase = SpeechPhase.PRIVATE if self._private else SpeechPhase.IDLE

    def begin_playback(self) -> None:
        self.phase = SpeechPhase.PLAYING

    def finish_playback(self) -> None:
        if self.phase is SpeechPhase.PLAYING:
            self.phase = SpeechPhase.PRIVATE if self._private else SpeechPhase.IDLE

    def _append_utterance(self, pcm: bytes) -> None:
        maximum_bytes = (
            self._buffer.audio_format.bytes_per_second * self._settings.maximum_utterance_seconds
        )
        remaining = maximum_bytes - len(self._utterance)
        if remaining > 0:
            self._utterance.extend(pcm[:remaining])

    def _reset_utterance(self) -> None:
        self._utterance.clear()
        self._speech_started = False
        self._silence_ms = 0

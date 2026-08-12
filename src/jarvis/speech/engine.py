import threading
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
    AWARE = "aware"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    PLAYING = "playing"


@dataclass(frozen=True)
class WakeDetected:
    pre_roll_pcm: bytes


@dataclass(frozen=True)
class UtteranceReady:
    pcm: bytes
    ambient: bool = False


@dataclass(frozen=True)
class BargeIn:
    captured_pcm: bytes


SpeechEvent = WakeDetected | UtteranceReady | BargeIn


class AwarenessMode(StrEnum):
    NORMAL = "normal"
    PRIVATE = "private"
    MEETING = "meeting"
    LECTURE = "lecture"
    AMBIENT = "ambient"


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
        self._mode = AwarenessMode.NORMAL
        self._utterance = bytearray()
        self._ambient_utterance = False
        self._speech_started = False
        self._silence_ms = 0
        self._lock = threading.RLock()
        self.phase = SpeechPhase.IDLE

    def ingest(self, pcm: bytes) -> SpeechEvent | None:
        with self._lock:
            return self._ingest(pcm)

    def _ingest(self, pcm: bytes) -> SpeechEvent | None:
        self._buffer.append(pcm)
        if self.phase is SpeechPhase.PLAYING and self._vad.is_speech(pcm):
            self._playback.cancel()
            self._reset_utterance()
            self._append_utterance(pcm)
            self._speech_started = True
            self.phase = SpeechPhase.LISTENING
            return BargeIn(captured_pcm=pcm)

        if self.phase in {SpeechPhase.IDLE, SpeechPhase.PRIVATE, SpeechPhase.AWARE}:
            if self._wake_word.detect(pcm):
                self._reset_utterance()
                self.phase = SpeechPhase.LISTENING
                return WakeDetected(pre_roll_pcm=self._buffer.snapshot())
            if self.phase is SpeechPhase.AWARE and self._vad.is_speech(pcm):
                self._reset_utterance()
                self._append_utterance(pcm)
                self._speech_started = True
                self._ambient_utterance = True
                self.phase = SpeechPhase.LISTENING
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
            ambient = self._ambient_utterance
            self._reset_utterance()
            self.phase = SpeechPhase.TRANSCRIBING
            return UtteranceReady(pcm=pcm_result, ambient=ambient)
        return None

    def set_private(self, enabled: bool) -> None:
        self.set_mode(AwarenessMode.PRIVATE if enabled else AwarenessMode.NORMAL)

    def set_mode(self, mode: AwarenessMode) -> None:
        with self._lock:
            self._mode = mode
            self._buffer.set_private(mode is AwarenessMode.PRIVATE)
            self._reset_utterance()
            self.phase = self._resting_phase()

    def complete_transcription(self) -> None:
        with self._lock:
            if self.phase is not SpeechPhase.TRANSCRIBING:
                raise RuntimeError("speech engine is not transcribing")
            self.phase = self._resting_phase()

    def begin_playback(self) -> None:
        with self._lock:
            self.phase = SpeechPhase.PLAYING

    def finish_playback(self) -> None:
        with self._lock:
            if self.phase is SpeechPhase.PLAYING:
                self.phase = self._resting_phase()

    def _append_utterance(self, pcm: bytes) -> None:
        maximum_bytes = (
            self._buffer.audio_format.bytes_per_second * self._settings.maximum_utterance_seconds
        )
        remaining = maximum_bytes - len(self._utterance)
        if remaining > 0:
            self._utterance.extend(pcm[:remaining])

    def _reset_utterance(self) -> None:
        self._utterance.clear()
        self._ambient_utterance = False
        self._speech_started = False
        self._silence_ms = 0

    def _resting_phase(self) -> SpeechPhase:
        if self._mode is AwarenessMode.PRIVATE:
            return SpeechPhase.PRIVATE
        if self._mode in {
            AwarenessMode.MEETING,
            AwarenessMode.LECTURE,
            AwarenessMode.AMBIENT,
        }:
            return SpeechPhase.AWARE
        return SpeechPhase.IDLE

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class VoiceActivityDetector(Protocol):
    def is_speech(self, pcm: bytes) -> bool: ...


class SpeechTranscriber(Protocol):
    async def transcribe(self, pcm: bytes) -> str: ...


class SpeechInput(Protocol):
    async def ingest(self, device_id: str, pcm: bytes) -> str | None: ...

    async def reset(self, device_id: str) -> None: ...


class RemoteSpeechSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_rate: int = Field(default=16_000, ge=8_000, le=48_000)
    sample_width_bytes: int = Field(default=2, ge=1, le=4)
    end_of_speech_silence_ms: int = Field(default=600, ge=20, le=3_000)
    maximum_utterance_seconds: int = Field(default=30, ge=1, le=300)
    maximum_frame_bytes: int = Field(default=7_680, ge=320, le=8_191)

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.sample_width_bytes


@dataclass
class _Utterance:
    pcm: bytearray = field(default_factory=bytearray)
    speech_started: bool = False
    silence_ms: float = 0


class RemoteSpeechInput:
    def __init__(
        self,
        *,
        vad: VoiceActivityDetector,
        transcriber: SpeechTranscriber,
        settings: RemoteSpeechSettings,
    ) -> None:
        self._vad = vad
        self._transcriber = transcriber
        self._settings = settings
        self._utterances: dict[str, _Utterance] = {}
        self._lock = asyncio.Lock()

    async def ingest(self, device_id: str, pcm: bytes) -> str | None:
        if not device_id or len(device_id) > 128:
            raise ValueError("device ID is invalid")
        if (
            not pcm
            or len(pcm) > self._settings.maximum_frame_bytes
            or len(pcm) % self._settings.sample_width_bytes != 0
        ):
            raise ValueError("remote PCM frame is invalid")

        async with self._lock:
            utterance = self._utterances.setdefault(device_id, _Utterance())
            speech = self._vad.is_speech(pcm)
            if not utterance.speech_started and not speech:
                return None
            utterance.speech_started = utterance.speech_started or speech
            maximum_bytes = (
                self._settings.bytes_per_second * self._settings.maximum_utterance_seconds
            )
            remaining = maximum_bytes - len(utterance.pcm)
            utterance.pcm.extend(pcm[:remaining])
            if speech:
                utterance.silence_ms = 0
            else:
                utterance.silence_ms += len(pcm) / self._settings.bytes_per_second * 1_000

            complete = (
                utterance.silence_ms >= self._settings.end_of_speech_silence_ms
                or len(utterance.pcm) >= maximum_bytes
            )
            if not complete:
                return None
            captured = bytes(utterance.pcm)
            self._utterances[device_id] = _Utterance()

        transcript = (await self._transcriber.transcribe(captured)).strip()
        return transcript or None

    async def reset(self, device_id: str) -> None:
        async with self._lock:
            self._utterances.pop(device_id, None)

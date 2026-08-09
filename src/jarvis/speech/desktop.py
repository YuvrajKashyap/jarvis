import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from jarvis.speech.engine import (
    AwarenessMode,
    BargeIn,
    SpeechCoordinator,
    UtteranceReady,
    WakeDetected,
)


class MicrophoneCapture(Protocol):
    def start(self, on_frame: Callable[[bytes], None]) -> None: ...

    def stop(self) -> None: ...


class SpeechTranscriber(Protocol):
    async def transcribe(self, pcm: bytes) -> str: ...


@dataclass(frozen=True)
class DesktopWake:
    occurred_at: datetime


@dataclass(frozen=True)
class DesktopTranscript:
    text: str
    occurred_at: datetime


@dataclass(frozen=True)
class DesktopAmbientTranscript:
    text: str
    occurred_at: datetime


@dataclass(frozen=True)
class DesktopBargeIn:
    occurred_at: datetime


@dataclass(frozen=True)
class DesktopSpeechError:
    code: str
    occurred_at: datetime


DesktopSpeechEvent = (
    DesktopWake | DesktopTranscript | DesktopAmbientTranscript | DesktopBargeIn | DesktopSpeechError
)


class DesktopSpeechSource(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def next_event(self) -> DesktopSpeechEvent: ...

    async def set_private(self, enabled: bool) -> None: ...

    async def set_mode(self, mode: AwarenessMode) -> None: ...


class DesktopSpeechService:
    def __init__(
        self,
        *,
        microphone: MicrophoneCapture,
        coordinator: SpeechCoordinator,
        transcriber: SpeechTranscriber,
        audio_queue_size: int = 64,
        event_queue_size: int = 16,
    ) -> None:
        if audio_queue_size < 2 or event_queue_size < 2:
            raise ValueError("desktop speech queues must contain at least two entries")
        self._microphone = microphone
        self._coordinator = coordinator
        self._transcriber = transcriber
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=audio_queue_size)
        self._events: asyncio.Queue[DesktopSpeechEvent] = asyncio.Queue(maxsize=event_queue_size)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._worker = asyncio.create_task(self._run(), name="jarvis-desktop-speech")
        try:
            await asyncio.to_thread(self._microphone.start, self._accept_frame)
        except BaseException:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
            self._loop = None
            raise

    async def stop(self) -> None:
        worker = self._worker
        self._worker = None
        self._loop = None
        await asyncio.to_thread(self._microphone.stop)
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def next_event(self) -> DesktopSpeechEvent:
        return await self._events.get()

    async def set_private(self, enabled: bool) -> None:
        await asyncio.to_thread(self._coordinator.set_private, enabled)

    async def set_mode(self, mode: AwarenessMode) -> None:
        await asyncio.to_thread(self._coordinator.set_mode, mode)

    def _accept_frame(self, pcm: bytes) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._enqueue_frame, bytes(pcm))

    def _enqueue_frame(self, pcm: bytes) -> None:
        if self._audio_queue.full():
            with suppress(asyncio.QueueEmpty):
                self._audio_queue.get_nowait()
        self._audio_queue.put_nowait(pcm)

    async def _run(self) -> None:
        while True:
            pcm = await self._audio_queue.get()
            try:
                speech_event = await asyncio.to_thread(self._coordinator.ingest, pcm)
                if isinstance(speech_event, WakeDetected):
                    self._publish(DesktopWake(occurred_at=datetime.now(UTC)))
                elif isinstance(speech_event, BargeIn):
                    self._publish(DesktopBargeIn(occurred_at=datetime.now(UTC)))
                elif isinstance(speech_event, UtteranceReady):
                    await self._transcribe(speech_event.pcm, ambient=speech_event.ambient)
            except (OSError, RuntimeError, ValueError):
                self._publish(
                    DesktopSpeechError(
                        code="desktop_speech_failed",
                        occurred_at=datetime.now(UTC),
                    )
                )

    async def _transcribe(self, pcm: bytes, *, ambient: bool) -> None:
        try:
            text = (await self._transcriber.transcribe(pcm)).strip()
        finally:
            self._coordinator.complete_transcription()
        if text:
            event = (
                DesktopAmbientTranscript(text=text, occurred_at=datetime.now(UTC))
                if ambient
                else DesktopTranscript(text=text, occurred_at=datetime.now(UTC))
            )
            self._publish(event)

    def _publish(self, event: DesktopSpeechEvent) -> None:
        if self._events.full():
            with suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
        self._events.put_nowait(event)

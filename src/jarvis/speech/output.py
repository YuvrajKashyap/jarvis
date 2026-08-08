import asyncio
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SynthesizedAudio:
    sample_rate: int
    pcm_s16le: bytes

    def __post_init__(self) -> None:
        if self.sample_rate < 8_000 or self.sample_rate > 96_000:
            raise ValueError("audio sample rate must be between 8000 and 96000 Hz")
        if not self.pcm_s16le or len(self.pcm_s16le) % 2:
            raise ValueError("audio must contain complete 16-bit PCM samples")


class SpeechSynthesizer(Protocol):
    async def synthesize(self, text: str) -> SynthesizedAudio: ...


class AudioSink(Protocol):
    async def play(self, audio: SynthesizedAudio) -> None: ...

    async def cancel(self) -> None: ...


class SpeechOutputSession(Protocol):
    async def push(self, text: str) -> None: ...

    async def finish(self) -> None: ...

    async def cancel(self) -> None: ...


class SpeechOutputFactory(Protocol):
    def open(
        self,
        *,
        device_id: str,
        send_phone_pcm: Callable[[bytes], Awaitable[None]],
    ) -> SpeechOutputSession: ...


class PhonePcmSink:
    def __init__(
        self,
        sender: Callable[[bytes], Awaitable[None]],
        *,
        sample_rate: int = 24_000,
        frame_bytes: int = 4_096,
    ) -> None:
        if frame_bytes < 512 or frame_bytes > 8_192 or frame_bytes % 2:
            raise ValueError("phone PCM frame size must be even and between 512 and 8192 bytes")
        self._sender = sender
        self._sample_rate = sample_rate
        self._frame_bytes = frame_bytes
        self._cancelled = False

    async def play(self, audio: SynthesizedAudio) -> None:
        if audio.sample_rate != self._sample_rate:
            raise ValueError("phone audio must use the negotiated fixed sample rate")
        if not self._cancelled:
            for offset in range(0, len(audio.pcm_s16le), self._frame_bytes):
                if self._cancelled:
                    return
                await self._sender(audio.pcm_s16le[offset : offset + self._frame_bytes])

    async def cancel(self) -> None:
        self._cancelled = True


class StreamingSpeechOutput:
    def __init__(self, *, synthesizer: SpeechSynthesizer, desktop_sink: AudioSink) -> None:
        self._synthesizer = synthesizer
        self._desktop_sink = desktop_sink

    def open(
        self,
        *,
        device_id: str,
        send_phone_pcm: Callable[[bytes], Awaitable[None]],
    ) -> SpeechOutputSession:
        sink = self._desktop_sink if device_id == "desktop" else PhonePcmSink(send_phone_pcm)
        return StreamingSpeechSession(synthesizer=self._synthesizer, sink=sink)


class StreamingSpeechSession:
    """Turns streamed text into bounded, cancellable clause-level speech work."""

    def __init__(
        self,
        *,
        synthesizer: SpeechSynthesizer,
        sink: AudioSink,
        queue_size: int = 8,
        maximum_buffer_characters: int = 1_000,
    ) -> None:
        if queue_size < 1 or queue_size > 100:
            raise ValueError("speech queue size must be between 1 and 100")
        if maximum_buffer_characters < 100 or maximum_buffer_characters > 10_000:
            raise ValueError("speech buffer must be between 100 and 10000 characters")
        self._synthesizer = synthesizer
        self._sink = sink
        self._clauses: asyncio.Queue[str | None] = asyncio.Queue(maxsize=queue_size)
        self._maximum_buffer_characters = maximum_buffer_characters
        self._buffer = ""
        self._worker: asyncio.Task[None] | None = None
        self._finished = False
        self.cancelled = False

    async def push(self, text: str) -> None:
        if self.cancelled or self._finished:
            return
        self._buffer += text
        clauses, self._buffer = _complete_clauses(self._buffer)
        if len(self._buffer) > self._maximum_buffer_characters:
            split_at = _preferred_split(self._buffer, self._maximum_buffer_characters)
            clauses.append(self._buffer[:split_at].strip())
            self._buffer = self._buffer[split_at:].lstrip()
        if clauses:
            self._ensure_worker()
            for clause in clauses:
                if clause:
                    await self._clauses.put(clause)

    async def finish(self) -> None:
        if self.cancelled or self._finished:
            return
        self._finished = True
        self._ensure_worker()
        remaining = self._buffer.strip()
        self._buffer = ""
        if remaining:
            await self._clauses.put(remaining)
        await self._clauses.put(None)
        worker = self._worker
        if worker is not None:
            await worker

    async def cancel(self) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        self._buffer = ""
        worker = self._worker
        if worker is not None:
            worker.cancel()
        await self._sink.cancel()
        if worker is not None:
            with suppress(asyncio.CancelledError):
                await worker
        while not self._clauses.empty():
            with suppress(asyncio.QueueEmpty):
                self._clauses.get_nowait()

    def _ensure_worker(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="jarvis-speech-output")

    async def _run(self) -> None:
        while True:
            clause = await self._clauses.get()
            if clause is None:
                return
            audio = await self._synthesizer.synthesize(clause)
            if self.cancelled:
                return
            await self._sink.play(audio)


def _complete_clauses(text: str) -> tuple[list[str], str]:
    clauses: list[str] = []
    consumed = 0
    for match in re.finditer(r".+?[.!?](?:[\"']?)(?:\s+|$)", text, flags=re.DOTALL):
        clauses.append(match.group(0).strip())
        consumed = match.end()
    return clauses, text[consumed:]


def _preferred_split(text: str, limit: int) -> int:
    window = text[:limit]
    for separator in ("; ", ", ", " "):
        index = window.rfind(separator)
        if index >= limit // 2:
            return index + len(separator)
    return limit

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int
    channels: int
    sample_width_bytes: int

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.channels <= 0 or self.sample_width_bytes <= 0:
            raise ValueError("audio format values must be positive")

    @property
    def sample_frame_bytes(self) -> int:
        return self.channels * self.sample_width_bytes

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.sample_frame_bytes


class AudioRingBuffer:
    def __init__(self, *, audio_format: AudioFormat, duration_seconds: int) -> None:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        self.audio_format = audio_format
        self.capacity_bytes = audio_format.bytes_per_second * duration_seconds
        self._buffer = bytearray(self.capacity_bytes)
        self._write_index = 0
        self._count = 0
        self._lock = threading.RLock()
        self.is_private = False

    def append(self, frame: bytes) -> None:
        if len(frame) % self.audio_format.sample_frame_bytes != 0:
            raise ValueError("PCM frame must be aligned to complete sample frames")
        if not frame:
            return
        with self._lock:
            if self.is_private:
                return
            if len(frame) >= self.capacity_bytes:
                self._buffer[:] = frame[-self.capacity_bytes :]
                self._write_index = 0
                self._count = self.capacity_bytes
                return

            first_length = min(len(frame), self.capacity_bytes - self._write_index)
            self._buffer[self._write_index : self._write_index + first_length] = frame[
                :first_length
            ]
            remaining = len(frame) - first_length
            if remaining:
                self._buffer[:remaining] = frame[first_length:]
            self._write_index = (self._write_index + len(frame)) % self.capacity_bytes
            self._count = min(self.capacity_bytes, self._count + len(frame))

    def snapshot(self, *, seconds: float | None = None) -> bytes:
        if seconds is not None and seconds < 0:
            raise ValueError("seconds cannot be negative")
        with self._lock:
            requested = self._count
            if seconds is not None:
                requested = min(requested, int(seconds * self.audio_format.bytes_per_second))
                requested -= requested % self.audio_format.sample_frame_bytes
            if requested == 0:
                return b""
            start = (self._write_index - requested) % self.capacity_bytes
            if start + requested <= self.capacity_bytes:
                return bytes(self._buffer[start : start + requested])
            first = bytes(self._buffer[start:])
            remainder = requested - len(first)
            return first + bytes(self._buffer[:remainder])

    def set_private(self, enabled: bool) -> None:
        with self._lock:
            self.is_private = enabled
            if enabled:
                self._buffer[:] = b"\x00" * self.capacity_bytes
                self._write_index = 0
                self._count = 0

    @property
    def buffered_seconds(self) -> float:
        with self._lock:
            return self._count / self.audio_format.bytes_per_second

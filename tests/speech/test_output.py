import asyncio

import pytest

from jarvis.speech.output import (
    PhonePcmSink,
    StreamingSpeechOutput,
    StreamingSpeechSession,
    SynthesizedAudio,
)


class FakeSynthesizer:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.block = asyncio.Event()
        self.block.set()

    async def synthesize(self, text: str) -> SynthesizedAudio:
        self.requests.append(text)
        await self.block.wait()
        return SynthesizedAudio(sample_rate=24_000, pcm_s16le=text.encode("utf-16le"))


class FakeSink:
    def __init__(self) -> None:
        self.played: list[bytes] = []
        self.cancelled = 0

    async def play(self, audio: SynthesizedAudio) -> None:
        self.played.append(audio.pcm_s16le)

    async def cancel(self) -> None:
        self.cancelled += 1


@pytest.mark.asyncio
async def test_streaming_output_speaks_complete_clauses_before_turn_finishes() -> None:
    synthesizer = FakeSynthesizer()
    sink = FakeSink()
    session = StreamingSpeechSession(synthesizer=synthesizer, sink=sink)

    await session.push("Your build is green. The next ")
    await asyncio.sleep(0)

    assert synthesizer.requests == ["Your build is green."]
    await session.push("step is packaging.")
    await session.finish()
    assert synthesizer.requests == ["Your build is green.", "The next step is packaging."]
    assert sink.played == [
        "Your build is green.".encode("utf-16le"),
        "The next step is packaging.".encode("utf-16le"),
    ]


@pytest.mark.asyncio
async def test_cancel_stops_queued_speech_and_cancels_current_playback() -> None:
    synthesizer = FakeSynthesizer()
    synthesizer.block.clear()
    sink = FakeSink()
    session = StreamingSpeechSession(synthesizer=synthesizer, sink=sink)
    await session.push("First clause. Second clause.")
    await asyncio.sleep(0)

    await session.cancel()

    assert sink.cancelled == 1
    assert sink.played == []
    assert session.cancelled is True


def test_audio_rejects_malformed_pcm() -> None:
    with pytest.raises(ValueError, match="16-bit"):
        SynthesizedAudio(sample_rate=24_000, pcm_s16le=b"\x01")
    with pytest.raises(ValueError, match="sample rate"):
        SynthesizedAudio(sample_rate=1_000, pcm_s16le=b"\x01\x00")
    with pytest.raises(ValueError, match="16-bit"):
        SynthesizedAudio(sample_rate=24_000, pcm_s16le=b"")


@pytest.mark.asyncio
async def test_phone_sink_enforces_fixed_pcm_contract_and_stops_after_cancel() -> None:
    frames: list[bytes] = []

    async def send(pcm: bytes) -> None:
        frames.append(pcm)

    sink = PhonePcmSink(send)
    await sink.play(SynthesizedAudio(sample_rate=24_000, pcm_s16le=b"\x01\x00"))
    await sink.cancel()
    await sink.play(SynthesizedAudio(sample_rate=24_000, pcm_s16le=b"\x02\x00"))

    assert frames == [b"\x01\x00"]
    with pytest.raises(ValueError, match="fixed sample rate"):
        await PhonePcmSink(send).play(SynthesizedAudio(sample_rate=22_050, pcm_s16le=b"\x01\x00"))


@pytest.mark.asyncio
async def test_phone_sink_chunks_long_audio_into_bounded_even_frames() -> None:
    frames: list[bytes] = []

    async def send(pcm: bytes) -> None:
        frames.append(pcm)

    await PhonePcmSink(send, frame_bytes=512).play(
        SynthesizedAudio(sample_rate=24_000, pcm_s16le=b"\x01\x00" * 600)
    )

    assert [len(frame) for frame in frames] == [512, 512, 176]
    assert b"".join(frames) == b"\x01\x00" * 600


def test_output_configuration_rejects_unbounded_queues_and_frames() -> None:
    with pytest.raises(ValueError, match="queue size"):
        StreamingSpeechSession(synthesizer=FakeSynthesizer(), sink=FakeSink(), queue_size=0)
    with pytest.raises(ValueError, match="speech buffer"):
        StreamingSpeechSession(
            synthesizer=FakeSynthesizer(),
            sink=FakeSink(),
            maximum_buffer_characters=20,
        )
    with pytest.raises(ValueError, match="frame size"):
        PhonePcmSink(lambda _pcm: asyncio.sleep(0), frame_bytes=513)


@pytest.mark.asyncio
async def test_long_unpunctuated_text_is_bounded_and_finish_is_idempotent() -> None:
    synthesizer = FakeSynthesizer()
    sink = FakeSink()
    session = StreamingSpeechSession(
        synthesizer=synthesizer,
        sink=sink,
        maximum_buffer_characters=100,
    )

    await session.push("word " * 30)
    await session.finish()
    await session.finish()
    await session.push("ignored after finish")

    assert len(synthesizer.requests) == 2
    assert "".join(synthesizer.requests).replace(" ", "") == ("word" * 30)


def test_output_factory_routes_desktop_and_phone_sinks() -> None:
    synthesizer = FakeSynthesizer()
    desktop = FakeSink()
    output = StreamingSpeechOutput(synthesizer=synthesizer, desktop_sink=desktop)

    async def send(_pcm: bytes) -> None:
        return None

    desktop_session = output.open(device_id="desktop", send_phone_pcm=send)
    phone_session = output.open(device_id="iphone", send_phone_pcm=send)

    assert isinstance(desktop_session, StreamingSpeechSession)
    assert isinstance(phone_session, StreamingSpeechSession)

from jarvis.speech.remote import RemoteSpeechInput, RemoteSpeechSettings

FRAME = b"\x01\x00" * 320
SILENCE = b"\x00\x00" * 320


class FakeVad:
    def is_speech(self, pcm: bytes) -> bool:
        return any(pcm)


class FakeTranscriber:
    def __init__(self) -> None:
        self.received: list[bytes] = []

    async def transcribe(self, pcm: bytes) -> str:
        self.received.append(pcm)
        return "What am I looking at?"


async def test_remote_pcm_is_segmented_and_transcribed_after_bounded_silence() -> None:
    transcriber = FakeTranscriber()
    speech = RemoteSpeechInput(
        vad=FakeVad(),
        transcriber=transcriber,
        settings=RemoteSpeechSettings(end_of_speech_silence_ms=40),
    )

    assert await speech.ingest("iphone", FRAME) is None
    assert await speech.ingest("iphone", SILENCE) is None
    result = await speech.ingest("iphone", SILENCE)

    assert result == "What am I looking at?"
    assert transcriber.received == [FRAME + SILENCE + SILENCE]


async def test_remote_pcm_rejects_misaligned_or_oversized_frames() -> None:
    speech = RemoteSpeechInput(
        vad=FakeVad(),
        transcriber=FakeTranscriber(),
        settings=RemoteSpeechSettings(),
    )

    for invalid in (b"\x00", b"\x00" * 8_192):
        try:
            await speech.ingest("iphone", invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid remote PCM was accepted")


async def test_remote_utterance_is_capped_even_without_silence() -> None:
    transcriber = FakeTranscriber()
    speech = RemoteSpeechInput(
        vad=FakeVad(),
        transcriber=transcriber,
        settings=RemoteSpeechSettings(maximum_utterance_seconds=1),
    )

    result = None
    for _index in range(50):
        result = await speech.ingest("iphone", FRAME)

    assert result == "What am I looking at?"
    assert len(transcriber.received[0]) == 16_000 * 2

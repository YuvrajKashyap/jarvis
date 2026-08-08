from jarvis.speech.audio import AudioFormat, AudioRingBuffer


def test_ring_capacity_is_exactly_120_seconds_of_configured_pcm() -> None:
    audio_format = AudioFormat(sample_rate=16_000, channels=1, sample_width_bytes=2)
    ring = AudioRingBuffer(audio_format=audio_format, duration_seconds=120)

    assert ring.capacity_bytes == 3_840_000
    assert ring.buffered_seconds == 0


def test_ring_overwrites_old_audio_and_returns_only_the_requested_tail() -> None:
    audio_format = AudioFormat(sample_rate=2, channels=1, sample_width_bytes=1)
    ring = AudioRingBuffer(audio_format=audio_format, duration_seconds=4)
    ring.append(bytes(range(12)))

    assert ring.snapshot() == bytes(range(4, 12))
    assert ring.snapshot(seconds=2) == bytes(range(8, 12))
    assert ring.buffered_seconds == 4


def test_private_mode_clears_audio_and_ignores_new_ambient_frames() -> None:
    ring = AudioRingBuffer(
        audio_format=AudioFormat(sample_rate=4, channels=1, sample_width_bytes=1),
        duration_seconds=3,
    )
    ring.append(b"ambient")

    ring.set_private(True)
    ring.append(b"ignored")

    assert ring.snapshot() == b""
    assert ring.is_private is True
    ring.set_private(False)
    ring.append(b"new!")
    assert ring.snapshot() == b"new!"


def test_pcm_frames_must_be_aligned_to_complete_samples() -> None:
    ring = AudioRingBuffer(
        audio_format=AudioFormat(sample_rate=16_000, channels=2, sample_width_bytes=2),
        duration_seconds=120,
    )

    try:
        ring.append(b"123")
    except ValueError as error:
        assert "aligned" in str(error)
    else:
        raise AssertionError("misaligned PCM was accepted")

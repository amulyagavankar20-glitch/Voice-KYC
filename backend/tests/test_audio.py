import io
import wave

from app.services import audio


def _wav_bytes(frames: bytes, *, rate: int, channels: int, width: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return buffer.getvalue()


def test_pcm_to_wav_roundtrip():
    pcm = b"\x00\x01" * 800
    data = audio.pcm_to_wav(pcm, sample_rate=16000)
    assert data[:4] == b"RIFF"
    with wave.open(io.BytesIO(data), "rb") as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.readframes(handle.getnframes()) == pcm


def test_wav_duration_ms():
    data = audio.pcm_to_wav(b"\x00\x00" * 16000, sample_rate=16000)
    assert audio.wav_duration_ms(data) == 1000
    assert audio.wav_duration_ms(b"not a wav") is None


def test_compress_audio_passes_through_normalised_wav():
    data = audio.pcm_to_wav(b"\x00\x00" * 1600, sample_rate=16000)
    result, content_type, duration, method = audio.compress_audio(data, content_type="audio/wav")
    assert content_type == "audio/wav"
    assert duration == 100
    assert method == "passthrough_16k_mono"
    assert result[:4] == b"RIFF"


def test_compress_audio_downmixes_and_resamples():
    # 48 kHz stereo, 100 ms.
    frames = b"\x00\x00\x00\x00" * 4800
    data = _wav_bytes(frames, rate=48000, channels=2, width=2)
    result, content_type, duration, method = audio.compress_audio(data, content_type="audio/wav")
    assert content_type == "audio/wav"
    assert method == "resampled_16k_mono"
    with wave.open(io.BytesIO(result), "rb") as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getnframes() == 1600
    assert duration == 100


def test_non_wav_is_stored_as_is_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(audio, "_ffmpeg_convert", lambda data, filename: None)
    result, content_type, duration, method = audio.compress_audio(
        b"ID3not-really-mp3", content_type="audio/mpeg", filename="voice.mp3"
    )
    assert result == b"ID3not-really-mp3"
    assert content_type == "audio/mpeg"
    assert duration is None
    assert method == "stored_as_is"

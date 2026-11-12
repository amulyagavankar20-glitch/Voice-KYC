"""Audio normalisation and WAV framing.

Browser recordings are decoded to mono WAV in the frontend already, but field
agents on low-bandwidth links may upload other containers. This module:

* frames raw PCM from the streaming WebSocket as a valid WAV file;
* normalises WAV input to 16 kHz, mono, 16-bit with the standard library;
* uses ffmpeg when available for non-WAV input, and otherwise stores the
  original bytes unchanged and says so (`method="stored_as_is"`).

Nothing here claims to verify integrity or identity.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

try:  # audioop is deprecated but still present in Python 3.11.
    import audioop
except ImportError:  # pragma: no cover - Python 3.13+
    audioop = None  # type: ignore[assignment]

TARGET_RATE = 16000
TARGET_CHANNELS = 1
TARGET_WIDTH = 2


def pcm_to_wav(pcm: bytes, *, sample_rate: int = TARGET_RATE, channels: int = 1, sample_width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def wav_duration_ms(data: bytes) -> int | None:
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or TARGET_RATE
            return round(frames / rate * 1000)
    except (wave.Error, EOFError):
        return None


def _is_wav(data: bytes, content_type: str | None) -> bool:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return True
    return bool(content_type and "wav" in content_type.lower())


def _normalise_wav(data: bytes) -> tuple[bytes, str]:
    """Return 16 kHz mono 16-bit WAV bytes and a method label."""
    with wave.open(io.BytesIO(data), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())

    if channels == TARGET_CHANNELS and width == TARGET_WIDTH and rate == TARGET_RATE:
        return pcm_to_wav(frames), "passthrough_16k_mono"

    if audioop is None:
        # Without audioop we can only re-frame, never resample/convert.
        return pcm_to_wav(frames, sample_rate=rate, channels=channels, sample_width=width), "reframed_no_resample"

    if channels > 1:
        frames = audioop.tomono(frames, width, 0.5, 0.5)
        channels = 1
    if width != TARGET_WIDTH:
        frames = audioop.lin2lin(frames, width, TARGET_WIDTH)
        width = TARGET_WIDTH
    if rate != TARGET_RATE:
        frames, _ = audioop.ratecv(frames, width, channels, rate, TARGET_RATE, None)
    return pcm_to_wav(frames), "resampled_16k_mono"


def _ffmpeg_convert(data: bytes, filename: str | None) -> bytes | None:
    binary = shutil.which("ffmpeg")
    if not binary:
        return None
    suffix = Path(filename or "upload.bin").suffix or ".bin"
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / f"input{suffix}"
        target = Path(directory) / "output.wav"
        source.write_bytes(data)
        process = subprocess.run(
            [
                binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-ac",
                str(TARGET_CHANNELS),
                "-ar",
                str(TARGET_RATE),
                "-acodec",
                "pcm_s16le",
                str(target),
            ],
            capture_output=True,
            timeout=60,
        )
        if process.returncode != 0 or not target.exists():
            return None
        return target.read_bytes()


def compress_audio(
    data: bytes,
    *,
    content_type: str | None,
    filename: str | None = None,
) -> tuple[bytes, str, int | None, str]:
    """Normalise audio for storage/upload.

    Returns ``(audio_bytes, content_type, duration_ms, method)``.
    """
    if _is_wav(data, content_type):
        try:
            normalised, method = _normalise_wav(data)
            return normalised, "audio/wav", wav_duration_ms(normalised), method
        except (wave.Error, EOFError):
            pass

    converted = _ffmpeg_convert(data, filename)
    if converted:
        return converted, "audio/wav", wav_duration_ms(converted), "ffmpeg_transcoded"

    # Honest fallback: keep the original container and label it. Prisma may
    # still accept it (MP3/OGG/FLAC/AAC/M4A are supported).
    return data, content_type or "application/octet-stream", None, "stored_as_is"

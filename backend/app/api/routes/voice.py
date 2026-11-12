import json
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.core.config import settings
from app.schemas.application import SynthesizeRequest
from app.services.audio import pcm_to_wav
from app.services.providers import ProviderNotConfigured, gnani_client

logger = logging.getLogger("voicekyc.voice")

router = APIRouter(prefix="/voice", tags=["voice"])

ALLOWED_AUDIO_TYPES = {
    "audio/webm",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/aac",
    "audio/flac",
    "audio/x-flac",
    "audio/ogg",
    "application/octet-stream",
}

LANGUAGES = ("hinglish", "marathi")


def media_type(content_type: str | None) -> str:
    """Strip parameters such as `audio/webm;codecs=opus` before allow-listing."""
    return (content_type or "").split(";", 1)[0].strip().lower()


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = Form("hinglish")):
    if language not in LANGUAGES:
        raise HTTPException(status_code=422, detail="Unsupported language.")
    if media_type(file.content_type) not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported audio content type.")
    if not settings.prisma_configured:
        raise HTTPException(status_code=503, detail="Prisma transcription is not configured.")

    # Read with a hard cap so an oversized upload cannot be buffered in full.
    content = await file.read(settings.max_audio_bytes + 1)
    if len(content) > settings.max_audio_bytes:
        raise HTTPException(status_code=413, detail="Audio file exceeds the configured upload limit.")
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded audio file is empty.")

    try:
        result = await gnani_client.transcribe_bytes(
            content,
            filename=file.filename or "recording.wav",
            content_type=file.content_type,
            language=language,
        )
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"text": result["text"], "provider": "prisma"}


@router.post("/synthesize")
async def synthesize(payload: SynthesizeRequest):
    if not settings.timbre_configured:
        raise HTTPException(status_code=503, detail="Timbre speech synthesis is not configured.")
    try:
        audio, content_type = await gnani_client.synthesize(payload.text, payload.language)
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=audio, media_type=content_type, headers={"Cache-Control": "no-store"})


@router.websocket("/stream")
async def stream(websocket: WebSocket):
    """App-level streaming STT.

    The browser sends 16-bit mono PCM chunks. The server buffers short windows
    (default 6 s, hard-capped below Prisma's 30 s limit) and transcribes each
    window with the batch Prisma endpoint. This removes the single-clip ceiling
    and returns progressive text, without guessing an undocumented vendor
    streaming protocol.
    """
    await websocket.accept()
    language = websocket.query_params.get("language", "hinglish")
    if language not in LANGUAGES:
        await websocket.send_json({"type": "error", "detail": "Unsupported language."})
        await websocket.close(code=1008)
        return
    if not settings.prisma_configured:
        await websocket.send_json({"type": "error", "detail": "Prisma transcription is not configured."})
        await websocket.close(code=1011)
        return

    sample_rate = 16000
    buffer = bytearray()
    parts: list[str] = []
    window_bytes = max(1, int(sample_rate * 2 * settings.stream_window_seconds))
    max_bytes = max(window_bytes, int(sample_rate * 2 * settings.stream_max_buffer_seconds))

    async def flush_buffer(final: bool) -> None:
        nonlocal buffer
        if not buffer:
            if final:
                await websocket.send_json({"type": "final", "text": "", "text_so_far": " ".join(parts)})
            return
        wav = pcm_to_wav(bytes(buffer), sample_rate=sample_rate)
        buffer = bytearray()
        try:
            result = await gnani_client.transcribe_bytes(
                wav,
                filename="stream.wav",
                content_type="audio/wav",
                language=language,
            )
        except HTTPException as exc:
            await websocket.send_json({"type": "error", "detail": str(exc.detail)})
            return
        text = result.get("text", "")
        if text:
            parts.append(text)
        await websocket.send_json(
            {
                "type": "final" if final else "partial",
                "text": text,
                "text_so_far": " ".join(parts),
            }
        )

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("text") is not None:
                try:
                    payload = json.loads(message["text"])
                except ValueError:
                    await websocket.send_json({"type": "error", "detail": "Control messages must be JSON."})
                    continue
                kind = payload.get("type")
                if kind == "start":
                    requested = payload.get("sampleRate", sample_rate)
                    if isinstance(requested, int) and 8000 <= requested <= 48000:
                        sample_rate = requested
                        window_bytes = max(1, int(sample_rate * 2 * settings.stream_window_seconds))
                        max_bytes = max(window_bytes, int(sample_rate * 2 * settings.stream_max_buffer_seconds))
                    await websocket.send_json({"type": "ready", "sampleRate": sample_rate})
                elif kind == "flush":
                    await flush_buffer(final=True)
                elif kind == "stop":
                    await flush_buffer(final=True)
                    await websocket.send_json({"type": "done", "text": " ".join(parts)})
                    break
            elif message.get("bytes") is not None:
                buffer.extend(message["bytes"])
                if len(buffer) >= window_bytes or len(buffer) >= max_bytes:
                    await flush_buffer(final=False)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # The client can close between `receive()` and `send_json()`; nothing to
        # report and no state to persist.
        pass
    finally:
        logger.info(
            "voice.stream_closed",
            extra={"event": "voice.stream_closed", "provider": "prisma"},
        )

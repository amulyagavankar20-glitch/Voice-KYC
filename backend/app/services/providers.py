import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.core.http import http_client, request_with_retries
from app.core.metrics import metrics

logger = logging.getLogger("voicekyc.providers")

# Gnani Speech API language mapping. The app exposes friendly language names;
# the provider expects locale codes. Prisma v3.0 accepts a comma-separated list.
STT_LANGUAGE_CODES = {
    "hinglish": "hi-IN,en-IN",
    "marathi": "mr-IN",
}
TTS_LANGUAGE_CODES = {
    "hinglish": "hi-en",
    "marathi": "mr-IN",
}
# Timbre v2.5 voices are optimised for one language each; a v2.5 voice sent with
# another model is rejected. Override both via GNANI_TTS_MODEL/GNANI_TTS_VOICE.
DEFAULT_TTS_VOICES = {
    "hinglish": "Poorvi",
    "marathi": "Zahira",
}

DEFAULT_AUDIO_CONFIG = {
    "sample_rate": 24000,
    "num_channels": 1,
    "sample_width": 2,
    "container": "mp3",
    "bitrate": "64k",
}


class ProviderNotConfigured(Exception):
    pass


def _error_detail(response: httpx.Response) -> str:
    """Extract a useful message from Gnani's several error envelopes."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300] or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or payload)
        if isinstance(error, str):
            return str(payload.get("message") or error)
        detail = payload.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("message") or detail)
        if isinstance(detail, str):
            return detail
        if payload.get("message"):
            return str(payload["message"])
    return str(payload)[:300]


class GnaniClient:
    """HTTP adapter for the documented Gnani Prisma (STT) and Timbre (TTS) APIs."""

    def _url(self, path: str, capability: str) -> str:
        if not settings.gnani_api_key or not settings.gnani_api_base_url or not path:
            raise ProviderNotConfigured(
                f"Gnani {capability} is not configured. Add the shared API key, base URL and endpoint path to .env."
            )
        return f"{settings.gnani_api_base_url.rstrip('/')}/{path.lstrip('/')}"

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        # Gnani authenticates with X-API-Key-ID, not Authorization: Bearer.
        headers = {"X-API-Key-ID": settings.gnani_api_key}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _stt_language(self, language: str) -> str:
        code = STT_LANGUAGE_CODES.get(language, STT_LANGUAGE_CODES["hinglish"])
        # Prisma v2.5 accepts exactly one language; v3.0 accepts a list.
        if settings.gnani_stt_model != "prisma-v3.0" and "," in code:
            return code.split(",")[0]
        return code

    def _tts_voice(self, language: str) -> str:
        return settings.gnani_tts_voice or DEFAULT_TTS_VOICES.get(language, DEFAULT_TTS_VOICES["hinglish"])

    async def transcribe(self, audio: UploadFile, language: str) -> dict[str, Any]:
        content = await audio.read()
        return await self.transcribe_bytes(
            content,
            filename=audio.filename or "recording.wav",
            content_type=audio.content_type,
            language=language,
        )

    async def transcribe_bytes(
        self,
        content: bytes,
        *,
        filename: str = "recording.wav",
        content_type: str | None = "audio/wav",
        language: str = "hinglish",
    ) -> dict[str, Any]:
        """Transcribe raw bytes. Shared by the upload endpoint and streaming WS."""
        url = self._url(settings.gnani_prisma_transcribe_path, "Prisma transcription")
        if not content:
            raise HTTPException(status_code=400, detail="The uploaded audio file is empty.")
        files = {
            # Gnani's multipart field is `audio_file`, not `file`.
            "audio_file": (
                filename or "recording.wav",
                content,
                content_type or "application/octet-stream",
            )
        }
        data = {
            "language_code": self._stt_language(language),
            # ITN turns spoken numbers/dates/phone digits into written form, which
            # the deterministic field validators can then check.
            "format": "transcribe",
        }
        if settings.gnani_stt_model:
            data["model"] = settings.gnani_stt_model

        started = time.perf_counter()
        headers = {**self._headers(), "X-API-Request-ID": str(uuid.uuid4())}
        try:
            async with http_client(settings.gnani_request_timeout_seconds) as client:
                response = await request_with_retries(
                    client,
                    "POST",
                    url,
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=settings.gnani_request_timeout_seconds,
                    attempts=settings.provider_retry_attempts,
                    base_delay=settings.provider_retry_base_delay_seconds,
                    provider="prisma",
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            duration = round((time.perf_counter() - started) * 1000, 2)
            metrics.record_provider("prisma", duration_ms=duration, status_code=exc.response.status_code, error=True)
            logger.warning(
                "provider.prisma.failed",
                extra={
                    "event": "provider.prisma.failed",
                    "provider": "prisma",
                    "status_code": exc.response.status_code,
                    "duration_ms": duration,
                },
            )
            raise HTTPException(
                status_code=502,
                detail=f"Prisma request failed ({exc.response.status_code}): {_error_detail(exc.response)}",
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            duration = round((time.perf_counter() - started) * 1000, 2)
            metrics.record_provider("prisma", duration_ms=duration, error=True)
            logger.warning(
                "provider.prisma.error",
                extra={"event": "provider.prisma.error", "provider": "prisma", "duration_ms": duration},
            )
            raise HTTPException(
                status_code=502,
                detail="Could not complete Prisma transcription request.",
            ) from exc

        # An empty transcript is a valid response (silence or no speech), so do
        # not use truthiness here.
        text = payload.get("transcript")
        if text is None:
            text = payload.get("text")
        if text is None:
            text = payload.get("transcription")
        if not isinstance(text, str):
            duration = round((time.perf_counter() - started) * 1000, 2)
            metrics.record_provider("prisma", duration_ms=duration, status_code=response.status_code, error=True)
            raise HTTPException(
                status_code=502,
                detail="Prisma response did not contain a `transcript` string. Check the configured API response contract.",
            )
        duration = round((time.perf_counter() - started) * 1000, 2)
        metrics.record_provider("prisma", duration_ms=duration, status_code=response.status_code)
        logger.info(
            "provider.prisma.transcribed",
            extra={
                "event": "provider.prisma.transcribed",
                "provider": "prisma",
                "status_code": response.status_code,
                "duration_ms": duration,
            },
        )
        return {"text": text.strip(), "raw": payload}

    async def synthesize(self, text: str, language: str) -> tuple[bytes, str]:
        url = self._url(settings.gnani_timbre_synthesize_path, "Timbre speech synthesis")
        body = {
            "text": text,
            "voice": self._tts_voice(language),
            "model": settings.gnani_tts_model,
            "language": TTS_LANGUAGE_CODES.get(language, TTS_LANGUAGE_CODES["hinglish"]),
            "speed": 1.0,
            "audio_config": dict(DEFAULT_AUDIO_CONFIG),
        }

        started = time.perf_counter()
        headers = {**self._headers("application/json"), "X-API-Request-ID": str(uuid.uuid4())}
        try:
            async with http_client(settings.gnani_request_timeout_seconds) as client:
                response = await request_with_retries(
                    client,
                    "POST",
                    url,
                    headers=headers,
                    json=body,
                    timeout=settings.gnani_request_timeout_seconds,
                    attempts=settings.provider_retry_attempts,
                    base_delay=settings.provider_retry_base_delay_seconds,
                    provider="timbre",
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            duration = round((time.perf_counter() - started) * 1000, 2)
            metrics.record_provider("timbre", duration_ms=duration, status_code=exc.response.status_code, error=True)
            logger.warning(
                "provider.timbre.failed",
                extra={
                    "event": "provider.timbre.failed",
                    "provider": "timbre",
                    "status_code": exc.response.status_code,
                    "duration_ms": duration,
                },
            )
            raise HTTPException(
                status_code=502,
                detail=f"Timbre request failed ({exc.response.status_code}): {_error_detail(exc.response)}",
            ) from exc
        except httpx.HTTPError as exc:
            duration = round((time.perf_counter() - started) * 1000, 2)
            metrics.record_provider("timbre", duration_ms=duration, error=True)
            logger.warning(
                "provider.timbre.error",
                extra={"event": "provider.timbre.error", "provider": "timbre", "duration_ms": duration},
            )
            raise HTTPException(
                status_code=502,
                detail="Could not complete Timbre synthesis request.",
            ) from exc

        content_type = response.headers.get("content-type", "audio/mpeg").split(";")[0].strip()
        if "json" in content_type:
            duration = round((time.perf_counter() - started) * 1000, 2)
            metrics.record_provider("timbre", duration_ms=duration, status_code=response.status_code, error=True)
            raise HTTPException(
                status_code=502,
                detail="Timbre returned JSON instead of audio bytes. Check the TTS model/voice/language settings.",
            )
        duration = round((time.perf_counter() - started) * 1000, 2)
        metrics.record_provider("timbre", duration_ms=duration, status_code=response.status_code)
        logger.info(
            "provider.timbre.synthesized",
            extra={
                "event": "provider.timbre.synthesized",
                "provider": "timbre",
                "status_code": response.status_code,
                "duration_ms": duration,
            },
        )
        return response.content, content_type


gnani_client = GnaniClient()

"""Optional LLM-based KYC field extraction via Groq (GPT-OSS).

This module is the *only* place that talks to Groq. It is deliberately
soft-failing: any network, auth, timeout, or parse error returns ``None`` so
the caller can fall back to the deterministic rules extractor. It never
auto-approves and never fabricates values; the system prompt forbids it and
every candidate is still run through ``validate_field`` by the caller.

Speech (Prisma STT / Timbre TTS) is untouched by this module.
"""

import json
import logging
import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.http import http_client, request_with_retries
from app.services.intake import REQUIRED_FIELDS

logger = logging.getLogger("voicekyc.llm_extraction")

# Keys we are willing to accept from the model. Anything else is dropped.
ALLOWED_FIELDS = set(REQUIRED_FIELDS)

SYSTEM_PROMPT_TEMPLATE = """You are a KYC field extraction engine for rural/semi-urban Indian microfinance onboarding.
Given a conversation transcript (possibly Hinglish/Hindi/English), extract only the requested KYC fields.

Rules:
- Use only information explicitly stated by the applicant.
- If unclear, missing, or conflicting, set value to null and needs_clarification true.
- Do not invent document numbers, IDs, or amounts.
- Normalize when possible: phone to digits; income to monthly integer INR if clearly stated; dates to ISO (YYYY-MM-DD) if possible.
- Return ONLY valid JSON matching the schema. No markdown, no prose.

Requested fields: {fields}

Output JSON schema:
{{
  "fields": {{
    "<field_key>": {{
      "value": string | null,
      "needs_clarification": boolean,
      "raw_span": string | null
    }}
  }},
  "clarification_question": string | null,
  "notes": string | null
}}

Only include keys from the requested fields list. Omit nothing requested; use null + needs_clarification true when unknown."""


def _build_transcript(messages: list) -> str:
    lines: list[str] = []
    for message in messages:
        role = getattr(message, "role", None) or (message.get("role") if isinstance(message, dict) else None)
        content = getattr(message, "content", None) or (message.get("content") if isinstance(message, dict) else None)
        if not content:
            continue
        speaker = "Applicant" if role == "user" else "Assistant"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


def _safe_parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    # Strip markdown fences if the model ignored instructions.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # Find the first JSON object if there is trailing prose.
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        parsed = json.loads(cleaned)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _normalize_result(parsed: dict[str, Any], pending: list[str]) -> dict[str, Any] | None:
    if not isinstance(parsed.get("fields"), dict):
        return None
    fields_in = parsed["fields"]
    out_fields: dict[str, Any] = {}
    for key in pending:
        if key not in ALLOWED_FIELDS:
            continue
        entry = fields_in.get(key)
        if not isinstance(entry, dict):
            entry = {"value": None, "needs_clarification": True, "raw_span": None}
        value = entry.get("value")
        if value is not None and not isinstance(value, str):
            value = str(value)
        needs_clar = bool(entry.get("needs_clarification", value is None))
        raw_span = entry.get("raw_span")
        if raw_span is not None and not isinstance(raw_span, str):
            raw_span = str(raw_span)
        out_fields[key] = {
            "value": value.strip() if isinstance(value, str) else value,
            "needs_clarification": needs_clar,
            "raw_span": raw_span,
        }
    clarification = parsed.get("clarification_question")
    if clarification is not None and not isinstance(clarification, str):
        clarification = str(clarification)
    notes = parsed.get("notes")
    if notes is not None and not isinstance(notes, str):
        notes = str(notes)
    # Keep clarification short and safe.
    if isinstance(clarification, str) and len(clarification) > 400:
        clarification = clarification[:400]
    return {
        "fields": out_fields,
        "clarification_question": clarification,
        "notes": notes,
    }


async def extract_fields_llm(
    messages: list,
    language: str,
    pending_fields: list[str],
    current_question: str | None = None,
) -> dict[str, Any] | None:
    """Call Groq for structured extraction. Returns normalized dict or None on any failure."""
    if not settings.llm_configured:
        return None
    pending = [f for f in pending_fields if f in ALLOWED_FIELDS]
    if not pending:
        return None

    url = f"{settings.groq_base_url.rstrip('/')}/chat/completions"
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(fields=", ".join(pending))
    transcript = _build_transcript(messages)
    user_parts = [f"Language: {language}", f"Pending fields: {', '.join(pending)}"]
    if current_question:
        user_parts.append(f"Current question: {current_question}")
    user_parts.append("Transcript:\n" + transcript)
    user_content = "\n".join(user_parts)

    payload = {
        "model": settings.groq_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }

    async def _post(with_format: bool) -> httpx.Response:
        body_payload = dict(payload)
        if not with_format:
            body_payload.pop("response_format", None)
        async with http_client(settings.groq_request_timeout_seconds) as client:
            response = await request_with_retries(
                client,
                "POST",
                url,
                headers=headers,
                json=body_payload,
                timeout=settings.groq_request_timeout_seconds,
                attempts=1,
                base_delay=0,
                provider="groq",
            )
        return response

    try:
        response = await _post(True)
        if response.status_code == 400:
            # Some models/deployments reject response_format; retry in plain mode.
            response = await _post(False)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("llm_extraction.failed", extra={"event": "llm_extraction.failed", "error": str(exc)})
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("llm_extraction.failed", extra={"event": "llm_extraction.failed", "error": str(exc)})
        return None

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("llm_extraction.bad_shape")
        return None

    parsed = _safe_parse_json(content)
    if parsed is None:
        logger.warning("llm_extraction.invalid_json")
        return None
    normalized = _normalize_result(parsed, pending)
    if normalized is None:
        logger.warning("llm_extraction.invalid_schema")
        return None
    return normalized

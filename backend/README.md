# VoiceKYC Backend

## Setup

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows; use cp on macOS/Linux
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

Run the backend from `backend/`. Imports are `app.*`, `pytest.ini` sets `pythonpath=.`, and the default SQLite path is relative to the working directory.

## Current behavior

- SQLite persistence (records survive backend restarts); PostgreSQL is supported through `DATABASE_URL` and Compose.
- Consent-gated application creation.
- Sequential intake with deterministic syntax validation. There is no LLM extraction in this repository.
- A framing-strip extractor removes conversational lead-ins and always keeps the applicant's exact words as a fallback candidate. Accepted values are stored as applicant statements with `confidence: null`, `confidence_type: "not_assessed"`, `source: "applicant_statement"`, and `needs_review: true`.
- Escalating clarification: after a repeated unusable answer the reply includes a per-field, per-language example.
- Approval is blocked while required fields are missing.
- Prisma and Timbre routes return an explicit configuration error until `GNANI_API_KEY` is supplied; the documented base URL and paths are built-in defaults.
- App-level streaming STT over `WS /api/v1/voice/stream`: 16-bit mono PCM is buffered into short windows and transcribed with the batch Prisma endpoint. If the socket fails, the browser uses `POST /voice/transcribe`.
- Optional PAN/Aadhaar uploads: PAN shape check, Aadhaar Verhoeff checksum, masked numbers, and name/DOB cross-check. Consistent fields are still not identity verification.
- Transparent rule-based evidence summary (`/applications/{id}/assessment`) with reason codes and only manual-review recommendations.
- Per-field agent review (`verified`/`flagged`) stored in `reviewed_fields`; the applicant record keeps `needs_review: true`.
- Idempotent applicant messages via `client_message_id`.
- Resumable audio uploads via `/audio/init`, `/audio/{id}/chunk`, `/audio/{id}/complete`.
- Local media storage under `STORAGE_DIR` with traversal-safe paths.
- Provider calls retry transient failures with exponential backoff and emit structured JSON logs with request IDs.
- `GET /api/v1/metrics` exposes request, provider, application, uptime and storage counters.
- API tests use an isolated in-memory SQLite database and temporary storage; they never touch `backend/voicekyc.db`.

## Gnani integration configuration

The same `GNANI_API_KEY` is used for both Prisma speech-to-text and Timbre text-to-speech. The backend loads the project-root `.env` as well as `backend/.env`; `backend/.env` takes precedence when both define a value.

The adapter targets the documented Gnani Speech API: Prisma `POST https://api.vachana.ai/stt/v3` (multipart `audio_file` + `language_code`) and Timbre `POST https://api.vachana.ai/api/v1/tts/inference` (JSON with `model`, `voice`, `language`, `audio_config`). Both authenticate with the `X-API-Key-ID` header. These defaults live in `app/core/config.py`; normally only `GNANI_API_KEY` is required. Prisma returns a `transcript` string and Timbre returns audio bytes. REST transcription accepts clips up to 30 seconds.

Optional audio/OCR tooling:

- `ffmpeg` on `PATH` converts non-WAV uploads; otherwise the original container is stored and labelled `stored_as_is`.
- `tesseract` (or `TESSERACT_CMD`) reads image documents; otherwise send applicant-supplied document text.

Never commit `.env` or real applicant data.

## Schema changes

- New tables are created by `Base.metadata.create_all` at startup.
- New columns on existing tables must be added to `_COLUMNS` in `app/db/migrate.py` for the local SQLite database.
- For deployment, generate and review an Alembic revision from `backend/`:

```bash
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

See `migrations/README.md`.

## Tests

```bash
cd backend
pytest
```

69 tests cover intake validators, extraction, audio framing/normalisation, PAN/Aadhaar checks, document cross-checks, the evidence summary, resumable uploads, the streaming WebSocket, idempotent messages, field review, follow-ups, capabilities, and metrics.

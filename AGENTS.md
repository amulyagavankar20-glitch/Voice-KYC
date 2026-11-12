# AGENTS.md

VoiceKYC is a prototype for voice-first KYC intake (Hinglish/Marathi) plus human agent review. Backend is FastAPI + SQLAlchemy/SQLite (PostgreSQL in Compose); frontend is React 18 + Vite. There is no root package, task runner, lint, or typecheck config. CI runs the backend tests and the frontend build.

## Commands

Backend (run from `backend/`, not the repo root):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000
```

Frontend (run from `frontend/`):

```powershell
npm ci             # reproducible install from the committed package-lock.json
Copy-Item .env.example .env
npm run dev        # http://localhost:5173, proxies /api and WebSocket to :8000
npm run build      # verified; only other script: dev/build/preview. No test, lint, or typecheck.
```

Tests (69 tests: intake validators, extraction, audio, documents, scorecard, API/WS/DB integration):

```powershell
cd backend
pytest                                              # all
pytest tests/test_intake.py::test_income_normalized # single test
```

Run the backend from `backend/` because imports are `app.*`, `pytest.ini` sets `pythonpath=.`, and the default SQLite URL is relative to the working directory. Running uvicorn from the repo root creates a second `./voicekyc.db` and hides the existing `backend/voicekyc.db`.

Deployment and CI:

```powershell
docker compose up --build      # frontend :8080, backend :8000, PostgreSQL
cd backend; alembic upgrade head
```

## Project map

- `backend/app/main.py` — app wiring, CORS, request-ID/metrics middleware, shared HTTP client, startup schema evolution, all routers, error handler.
- `backend/app/core/` — `config.py` (settings), `http.py` (pooled client + retries), `logging.py` (JSON logs/request ID), `metrics.py` (in-process counters).
- `backend/app/db/` — engine/base/session and `migrate.py` (idempotent `ALTER TABLE ADD COLUMN`).
- `backend/app/api/routes/` — `applications.py` (intake, idempotency, detail, assessment, field review, follow-up, decision), `documents.py`, `audio.py` (direct + resumable), `voice.py` (batch STT/TTS + WebSocket), `health.py`, `ops.py` (`/metrics`).
- `backend/app/services/intake.py` — 8 required fields, bilingual prompts, deterministic syntax validation, field records, clarification/follow-up templates.
- `backend/app/services/extraction.py` — deterministic framing-strip candidates and clarification-attempt derivation. There is no LLM extraction path.
- `backend/app/services/documents.py` — PAN shape, Aadhaar Verhoeff, name/DOB comparison, cross-check.
- `backend/app/services/scorecard.py` — transparent rule-based evidence summary and reason codes.
- `backend/app/services/audio.py` — PCM→WAV, 16 kHz mono normalisation, optional ffmpeg.
- `backend/app/services/ocr.py` — optional Tesseract adapter with a no-OCR fallback.
- `backend/app/services/storage.py` — safe generated file paths, append/read/delete.
- `backend/app/services/providers.py` — isolated Gnani Prisma/Timbre adapters.
- `backend/app/models/application.py` — `Application`, `ConversationMessage`, `IdentityDocument`, `AudioUpload`.
- `backend/migrations/` — Alembic environment; `alembic.ini` at `backend/`. Startup migration is not a substitute for reviewed deployment migrations.
- `frontend/src/App.jsx` — toggles the two screens; no router.
- `frontend/src/components/UserInterface.jsx` — consent, streaming PCM recorder + batch fallback, offline outbox, polling, PAN/Aadhaar upload, TTS playback.
- `frontend/src/components/AgentDashboard.jsx` — list/detail polling, evidence summary, documents, audio playback, field review, follow-up, decision.
- `frontend/src/api/client.js` — all HTTP/upload/WebSocket calls; base is `VITE_API_BASE_URL` (default `/api/v1`).
- `frontend/nginx.conf` + `frontend/Dockerfile`, `backend/Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml` — deployment/CI.

API (all under `/api/v1`): `POST /applications` (consent required), `POST /applications/{id}/messages` (optional `client_message_id`), `GET /applications`, `GET|DELETE /applications/{id}`, `POST /applications/{id}/decision`, `GET /applications/{id}/assessment`, `POST /applications/{id}/fields/{field}/review`, `POST /applications/{id}/follow-up`, `POST|GET|DELETE /applications/{id}/documents...`, `POST|GET|DELETE /applications/{id}/audio...`, `POST /voice/transcribe`, `POST /voice/synthesize`, `WS /voice/stream`, `GET /health`, `/capabilities`, `/metrics`.

## Flow and state

`in_progress` → (all 8 fields filled) `pending_review` → `approve` | `reject`. `request_info` is a valid decision; the next applicant message resets it to `in_progress`. Approval is rejected while any required field is missing. The agent decision is human-recorded; nothing here verifies identity or creditworthiness.

Adding or reordering intake fields means editing `REQUIRED_FIELDS`, `QUESTIONS`, `ANSWER_HINTS` for both languages, plus validation in `intake.py`. The frontend renders `extracted` generically, so no per-field UI change is needed. Cross-field guidance and examples were removed with the LLM extraction layer.

If a new model column is added, add it to `_COLUMNS` in `backend/app/db/migrate.py` for existing SQLite databases, and generate a reviewed Alembic revision for PostgreSQL. `Base.metadata.create_all` creates new tables but never alters existing ones.

## Configuration

- Settings load the project-root `.env` and `backend/.env`; `backend/.env` wins when both define a key, and process env wins over both.
- The root `.env` contains the live Gnani API key and is gitignored. Never print, commit, copy, or echo the key. If `backend/.env` also exists, a blank `GNANI_API_KEY=` there overrides the root value.
- Gnani is "configured" when the key plus base URL and endpoint paths are present. Base URL (`https://api.vachana.ai`) and paths (`/stt/v3`, `/api/v1/tts/inference`) are working defaults in `config.py`, so normally only `GNANI_API_KEY` is needed. Without Gnani config, voice endpoints return 503 and every voice UI path degrades to typing.
- Gnani auth is `X-API-Key-ID`, not `Authorization: Bearer`. Prisma uses multipart field `audio_file` + `language_code`; Timbre requires `model`, `voice`, `language`, and `audio_config`. Check https://docs.gnani.ai before changing the contract — do not reintroduce placeholders.
- Streaming is app-level: browser PCM → short windows → batch Prisma. Do not claim it is a vendor streaming contract. `STREAM_WINDOW_SECONDS` defaults to 6 and `STREAM_MAX_BUFFER_SECONDS` to 25 (below Prisma's 30-second limit).
- `DATABASE_URL` defaults to `sqlite:///./voicekyc.db`. Startup migration adds `reviewed_fields`, `assessment`, and `client_message_id` to existing databases. `backend/voicekyc.db` is local state.
- `STORAGE_DIR` defaults to `./data/uploads` (relative to the backend working directory). `MAX_AUDIO_UPLOAD_MB` (15) and `MAX_DOCUMENT_UPLOAD_MB` (10) bound uploads.
- OCR/ffmpeg are optional. Tesseract is used only if `TESSERACT_CMD` resolves; ffmpeg only if on `PATH`. Without them the API labels `applicant_supplied_text`/`stored_as_is`.
- `LOG_LEVEL` (default `INFO`), `PROVIDER_RETRY_ATTEMPTS` (3) and `PROVIDER_RETRY_BASE_DELAY_SECONDS` (0.5) tune logging/retries; `GNANI_REQUEST_TIMEOUT_SECONDS` defaults to 45.
- Compose reads the gitignored root `.env` for `GNANI_API_KEY` substitution. Compose defaults for passwords are development-only.

## Invariants — preserve these

- Never fabricate confidence. Fields stay `confidence: null`, `confidence_type: "not_assessed"`, `source: "applicant_statement"`, `needs_review: true`; the UI surfaces this as unassessed. Agent field review lives in `reviewed_fields` and must not rewrite the applicant record.
- Keep the scope honest: intake collects applicant statements for human review. Do not add autonomous approvals, identity-verification claims, or credit-risk scoring. The evidence summary is not a credit score.
- Document consistency is a review flag, not verification. Never label a cross-check as identity verification.
- Do not add a silent fallback for a configured provider. Provider errors surface as `502` on purpose. The only fallbacks allowed are the honest no-provider paths (typing, batch recording, applicant-supplied document text, stored-as-is audio).
- Do not reintroduce Evon or another semantic extraction layer without an explicit product decision and an evaluated endpoint.
- The agent API/dashboard has no authentication by design; do not expose it publicly or add real applicant data.
- Retries must remain idempotent: `client_message_id` for messages, exact offsets for audio chunks.
- Uploaded files must stay under `STORAGE_DIR`; use `services/storage.py` rather than constructing paths.
- Deleting an application must delete its stored files.

## Gotchas

- CORS allows `GET, POST, DELETE, OPTIONS` and the `X-Request-ID` header; adding another method/header requires updating `main.py`.
- Vite must proxy WebSockets (`ws: true` in `vite.config.js`); nginx needs the `Upgrade`/`Connection` headers (already in `frontend/nginx.conf`).
- `voice/transcribe` accepts only listed audio MIME types (parameters such as `;codecs=opus` are stripped first) and enforces `MAX_AUDIO_UPLOAD_MB`. Gnani rejects WebM; the browser now captures PCM and sends WAV (stream) or a WAV blob (batch fallback).
- Prisma batch transcription is limited to 30 seconds. Streaming windows are capped below that; the browser auto-stops at 29 seconds.
- The frontend recorder uses `ScriptProcessorNode` (deprecated but broadly supported) and requires `AudioContext` + microphone permission.
- The offline outbox is device-local `localStorage`. It preserves order and retries; do not add a non-idempotent send path beside it.
- `frontend/dist/` is not served by the backend. Use `npm run preview` or the nginx Docker image.
- `tests/test_api.py` overrides `get_db` with an in-memory SQLite engine and `tmp_path` storage because the production engine is bound at import time. Extend that fixture for new API tests; never point tests at `backend/voicekyc.db` or the real uploads directory.
- Tests do not run the lifespan, so the shared HTTP client is `None` and a short-lived client is created per provider call; the migration helper also does not run there (`create_all` builds the full schema).
- An initial Alembic revision (`e0013f9cb6b6`) is committed under `backend/migrations/versions/`. Regenerate/review a fresh revision if the models change; `alembic upgrade head` is now usable against an empty database.

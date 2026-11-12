# Changelog

## 2.1.0 - 2026-11-12

### Removed

- Removed the unusable Evon chat-completions integration (configuration, adapter, schema/prompt layer, tests, environment examples). The repository has no semantic LLM extraction path.

### Intake and applicant experience

- Added a conservative deterministic framing-strip extractor that removes conversational lead-ins and always keeps the applicant's untouched utterance as a fallback candidate, so a value can only be less specific than what was said, never invented.
- Clarification rounds now escalate: after a repeated unusable answer the reply includes a concrete per-field, per-language example.
- Added an app-level streaming STT WebSocket that buffers 16 kHz mono PCM into short windows and transcribes each window with the batch Prisma endpoint.
- The browser falls back to one batch WAV recording if the WebSocket or provider is unavailable; typing always remains available.
- Added an offline outbox in `localStorage` with ordered retry and idempotent `client_message_id` delivery.
- Applicant polling now surfaces agent follow-ups and status changes without a manual refresh.
- Added an optional PAN/Aadhaar upload panel with explicit cross-check status.

### Documents, assessment and review

- Added PAN shape validation and Aadhaar Verhoeff checksum validation.
- Added token-wise name comparison and parsed date-of-birth comparison; a shared surname alone is not a match.
- Cross-check results are `consistent`, `inconsistent`, or `not_checked`; a mismatch is a review flag, never identity verification.
- Added a transparent rule-based evidence summary with per-reason points. Recommendations are limited to `standard_manual_review` and `priority_manual_review`.
- Added per-field agent review (`verified`/`flagged`) stored separately from the applicant record, preserving `confidence: null` and `needs_review: true`.
- Added localised agent follow-up generation and delivery, including document-mismatch wording.
- Expanded the agent cockpit with evidence reasons, document status, audio playback, field-review badges, follow-up controls, live polling, and decision guards.

### Media, reliability and operations

- Added local media storage with traversal-safe paths and cleanup on delete.
- Added WAV normalisation to 16 kHz mono 16-bit, optional ffmpeg conversion, and honest stored-as-is fallback.
- Added resumable audio uploads with offset checks plus direct upload and playback.
- Added optional Tesseract OCR with an applicant-supplied-text fallback.
- Added a pooled `httpx.AsyncClient` created in the application lifespan.
- Added an in-process metrics registry, request/provider metrics, and `GET /api/v1/metrics`.
- Added idempotent startup schema evolution for the local SQLite database.
- Added Alembic scaffolding for reviewed deployment migrations.

### Deployment and tooling

- Added backend and frontend Dockerfiles, nginx SPA/API/WebSocket configuration, and Docker Compose with PostgreSQL 16.
- Added a GitHub Actions workflow for backend compile/tests and frontend install/build.
- Verified the frontend production build and expanded the backend suite to 69 passing tests.

### Earlier hardening retained in this release

- Fixed Gnani integration against the documented Speech API: `X-API-Key-ID` auth, `/stt/v3`, `/api/v1/tts/inference`, multipart `audio_file` + `language_code`, and Timbre `model`/`voice`/`language`/`audio_config`.
- Browser recordings are re-encoded as mono WAV because Gnani rejects WebM.
- Outbound provider calls retry transient failures with exponential backoff.
- Structured JSON logs carry per-request correlation IDs (`X-Request-ID`).
- Unhandled exceptions return a JSON 500 with a request ID instead of an empty response.
- `updated_at` changes after validation failures and `request_info` resumes.
- Conversation transcripts are ordered by message ID.
- Audio content types are parsed before `;` parameters.
- Upload size checks read with a hard cap instead of buffering arbitrary sizes.
- Income parsing understands Indian units (`1.5 lakh`, `50k`, `2 crore`) and comma-grouped amounts.
- Date of birth parsing accepts `DD/MM/YYYY`, `YYYY-MM-DD`, `DD Month YYYY`, Devanagari digits, and rejects future dates.
- Phone validation handles `+91`, `0091`, and leading-zero formats.
- Frontend requests have timeouts, network-error messages, and safe non-JSON parsing.
- Frontend releases microphone tracks, recorders, timers, audio players, and object URLs on stop/unmount.
- Frontend ignores stale transcription and detail responses after a session or selection change.
- Blank messages and blank TTS text are rejected with 422.
- Added API/DB integration tests using isolated in-memory SQLite and temporary storage.

## 2.0.0 - Codebase restructuring

- Replaced the single-file in-memory FastAPI implementation with modular API, schema, model, database, configuration and service layers.
- Added SQLite persistence and consent-gated application creation.
- Added request validation, field syntax checks, safe decision validation and proper HTTP error handling.
- Removed fixed confidence scores; fields explicitly require human verification.
- Added configurable Prisma and Timbre provider adapters without inventing account-specific API paths.
- Added microphone recording and voice-service controls to the applicant interface.
- Centralized frontend API requests and corrected Vite API proxy configuration.
- Added tests, environment templates, architecture notes and setup documentation.

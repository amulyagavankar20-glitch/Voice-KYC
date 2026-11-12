# VoiceKYC — Idea and Solution Document

## 1. Problem statement

### 1.1 The real-world problem

Microfinance institutions (MFIs), small banks, and cooperative lenders in India serve millions of applicants who are not comfortable with English-language, document-heavy, self-service digital onboarding. A typical KYC (Know Your Customer) intake asks the applicant to:

- read and understand formal English forms,
- type names, addresses, occupations, and income into small fields,
- upload high-quality scans of PAN and Aadhaar,
- sit through a long, uninterrupted session with no human help.

For a large share of the target population this is effectively a **digital wall**: the applicant gives up, the field agent fills the form on their behalf (introducing errors and fraud risk), or the application is abandoned. The institution loses a customer and the applicant loses access to credit.

Even where voice is used, most "voice KYC" products are either:

- a thin IVR that records a single audio clip and sends it to a human transcriber (slow, expensive, no structure), or
- a fully automated, black-box decision engine that claims to "verify" identity from voice (unsafe, unverifiable, and not regulatorily acceptable).

Neither is honest about what a machine can and cannot do.

### 1.2 Specific pain points we observed

1. **Language mismatch.** Forms are in English; applicants think and speak in Hinglish or Marathi. Transliteration and code-mixing make typed entry error-prone.
2. **Noisy / low-bandwidth environments.** Rural connectivity is intermittent. A long single upload fails and loses everything. There is no resume.
3. **Long forms cause abandonment.** Eight or more required fields in one session, with no progress feedback, leads to drop-off.
4. **Unverified automation claims.** Products that say "AI verified your identity" from a photo or a voice clip are making claims they cannot support. Regulators and reviewers cannot audit them.
5. **No audit trail for the human reviewer.** Agents receive a flat record with no evidence of what was said, no document cross-check, and no reason codes. Review becomes guesswork.
6. **Fabricated confidence.** Many systems show "95% confidence" that is not calibrated against any dataset. This misleads reviewers into trusting wrong values.
7. **No graceful degradation.** If the speech provider is down, the whole flow dies. There is no honest fallback to typing.
8. **No separation of applicant record and reviewer judgment.** Editing an applicant's stated value during review destroys the original statement and the audit trail.

### 1.3 The core question

> How do we collect structured KYC information from applicants who cannot or will not type English forms, while keeping every claim the system makes **honest, auditable, and human-reviewed**?

That question — not "how do we automate KYC" — is the problem we set out to solve.

## 2. How we came up with the solution

### 2.1 Starting point: a voice-first, human-in-the-loop prototype

We started from the observation that the *collection* step and the *decision* step are different problems. Collecting an applicant's stated name, address, income, and purpose is a structured-data problem that voice can help with. Deciding whether that applicant is who they say they are, and whether they should receive credit, is a regulated human judgment that a prototype must never automate.

So the first design decision was: **the system collects statements; humans decide.** Everything else follows from that.

### 2.2 Choosing the voice layer

We needed speech-to-text and text-to-speech that work for Hinglish and Marathi. We evaluated the documented Gnani Speech API (Prisma for STT, Timbre for TTS) because:

- it is a documented, real endpoint with a published contract (not a placeholder),
- it supports Indian languages and code-mixed speech,
- authentication is a single shared key (`X-API-Key-ID`), which keeps configuration simple for a prototype.

We deliberately did **not** invent an undocumented streaming protocol. The vendor's native streaming contract is not part of the verified documentation, so we built an **app-level** streaming layer on top of the verified batch endpoint. This is a conscious honesty trade-off: we get progressive transcription and a 30-second clip ceiling avoidance, at the cost of not claiming "native vendor streaming."

### 2.3 Removing the LLM extraction layer

An earlier version of this prototype used an LLM (Evon) to extract a field value from a free-form utterance. We removed it completely because:

- the endpoint could not be configured or evaluated in our environment,
- a probabilistic extractor that "guesses" a field value from a sentence is exactly the kind of unverified automation we are trying to avoid,
- it created a false sense of intelligence: a confident-looking value that no one could audit.

Instead we built a **deterministic framing-strip extractor**: it removes conversational lead-ins ("mera naam … hai", "main kheti karta hoon", "my address is …") and keeps the applicant's untouched utterance as a fallback candidate. The syntax validators still decide. A value can only be *less specific* than what was said, never invented. This is slower and less clever, and that is the point.

### 2.4 Escalating clarification instead of looping

When a value does not validate, the system re-asks. Repeating the identical question is useless. So after a first failure, the reply adds a concrete per-field, per-language example ("Jaise: 12/03/1990"). This gives the applicant new information without inventing a value for them.

### 2.5 Confidence is reported as "not assessed," never fabricated

We removed every fixed confidence score. Accepted values are stored with `confidence: null`, `confidence_type: "not_assessed"`, `source: "applicant_statement"`, `needs_review: true`. The UI surfaces this as "Not independently assessed · Verify." This is the single most important invariant in the codebase: **no fabricated confidence, ever.**

### 2.6 Document cross-check, not verification

Applicants can upload PAN or Aadhaar text (or an image when Tesseract is available). We validate PAN shape (`AAAAA9999A`) and the Aadhaar Verhoeff checksum, then compare names (token-wise, so a shared surname alone is not a match) and dates of birth. The result is `consistent`, `inconsistent`, or `not_checked`. An inconsistency raises a review flag. It is never described as identity verification, because it is not: there is no liveness check, no government API lookup, no forgery detection.

### 2.7 Evidence summary, not a credit score

The agent cockpit shows a transparent, rule-based summary: completion, income band, occupation clarity, loan purpose, phone presence, document consistency. Every point is tied to a reason code with a numeric contribution. Recommendations are limited to `standard_manual_review` and `priority_manual_review`. There is no approve/reject recommendation, no score that claims to predict repayment. It ranks **how much reviewable evidence exists**, nothing more.

### 2.8 Agent field review separate from applicant record

When an agent marks a field `verified` or `flagged`, that judgment is stored in a separate `reviewed_fields` column. The applicant's record keeps `needs_review: true` and `confidence: null`. The original statement is never overwritten, so the audit trail is preserved.

### 2.9 Low-bandwidth and retry design

- Each applicant message carries a client-generated `client_message_id`. A repeat returns the current state with `duplicate: true` and adds no second turn. Retries are at-most-once.
- The browser outbox queues unsent answers in `localStorage` and flushes in order on reconnect and every 15 seconds. While queued answers exist, new answers join the queue to preserve order.
- Audio can be uploaded in offset-checked chunks (`/audio/init`, `/chunk`, `/complete`) and resumed after a dropped connection. A stale retry returns `409` with the expected offset.
- Applicant and agent screens poll every 8 seconds, so follow-ups and new records appear without a manual refresh.
- If the streaming socket or provider is unavailable, the browser falls back to one batch recording, and typing always works.

### 2.10 Honest provider handling

If a provider is configured, errors surface as `502`. There is **no silent fallback** to a fake or cached result. The only fallbacks are the honest no-provider paths: typing, batch recording, applicant-supplied document text, and stored-as-is audio. A configured provider that fails must be visible.

### 2.11 Scope honesty

The agent API/dashboard has no authentication by design, and we document that it must not be exposed publicly or used with real applicant data. The prototype is a demonstration of collection and assisted review, not a production KYC system.

## 3. Proposed solution (what we built)

VoiceKYC is a **voice-first, human-in-the-loop KYC intake prototype** with the following architecture:

### 3.1 Applicant experience

1. Applicant gives explicit consent.
2. A conversational assistant asks eight required fields in Hinglish or Marathi: full name, father's name, date of birth, address, occupation, monthly income, loan purpose, mobile number.
3. The applicant speaks or types. Speech is captured as 16 kHz mono PCM in the browser, streamed over a WebSocket, and transcribed in short windows. Each transcript is shown for review/editing before submission.
4. A deterministic framing-strip removes conversational lead-ins; the syntax validators decide whether the value is acceptable. If not, the field is re-asked with an example after the first failure.
5. Accepted values are stored as applicant statements with unassessed confidence and a review flag.
6. Progress is shown as a percentage and a field list. When all eight fields are present, the application moves to `pending_review`.
7. Optionally, the applicant uploads PAN or Aadhaar text (or an image). The system cross-checks it against what was said and shows the result honestly.
8. If the connection drops, answers are queued on the device and retried automatically. Audio can be resumed chunk by chunk.

### 3.2 Agent experience

1. The agent cockpit lists applications with status, progress, evidence score, and live updates.
2. Selecting an application loads the fields, the evidence summary with reason codes, documents with cross-check badges, stored audio with playback, and the full transcript.
3. The agent can mark each field `verified` or `flagged` with a note. This does not alter the applicant record.
4. The agent can generate and send a localised follow-up question (with document-mismatch wording when applicable), moving the application to `request_info`. The applicant's next answer resets it to `in_progress`.
5. The agent records `approve`, `request_info`, or `reject` with an optional note. Approval is blocked while required fields are missing.
6. Every decision is human-recorded. Nothing verifies identity or creditworthiness.

### 3.3 Backend

- FastAPI with modular routers: applications, documents, audio, voice, health, ops.
- SQLAlchemy persistence (SQLite locally, PostgreSQL in Compose) with idempotent startup column additions and Alembic scaffolding for deployment.
- Services: intake validation, deterministic extraction, document cross-check (PAN/Aadhaar/Verhoeff/name/DOB), scorecard, audio framing/normalisation, optional OCR, safe storage, Gnani provider adapters.
- Pooled HTTP client with bounded retry/backoff. Structured JSON logs with request correlation IDs. In-process metrics endpoint.
- Safe file storage under `STORAGE_DIR` with traversal protection and cleanup on delete.

### 3.4 Frontend

- React 18 + Vite, two screens (applicant / agent) toggled without a router.
- Streaming PCM recorder using `ScriptProcessorNode` with 29-second auto-stop.
- Offline outbox in `localStorage`.
- Generic rendering of extracted fields, so adding a field needs no UI change.
- All HTTP/WebSocket calls centralized in `api/client.js` with timeouts and retryable-error tagging.

### 3.5 Deployment

- Docker images for backend and frontend, nginx reverse proxy with WebSocket upgrade, Docker Compose with PostgreSQL 16 and health checks.
- GitHub Actions CI: backend compile + tests, frontend `npm ci` + build.

## 4. Impact of the solution

### 4.1 For applicants

- **Access.** People who cannot type English forms can complete intake by speaking in their own language. The wall becomes a conversation.
- **Clarity.** Progress is visible; each question is one field at a time; examples appear when an answer is unclear.
- **Resilience.** Intermittent connectivity no longer loses the whole session. Answers queue and retry; audio resumes.
- **Honesty.** The applicant is told plainly that this is information collection, not verification or approval. No false promises.

### 4.2 For agents and institutions

- **Auditability.** Every field carries the applicant's own words as evidence, a review status, and a timestamp. The original statement is never overwritten.
- **Faster, better review.** The evidence summary with reason codes and document cross-check badges surface what needs attention instead of dumping a flat record.
- **Consistent handling.** Deterministic validation and a single intake flow reduce per-agent variation and transcription errors.
- **No black box.** Reviewers can see exactly why a value was accepted or rejected (syntax rules, not a model score), and exactly what the document cross-check found.

### 4.3 For regulators and auditors

- **No fabricated confidence.** Confidence is reported as unassessed, not invented.
- **No autonomous decisions.** The system never approves, rejects, or verifies. A human records every decision.
- **Document cross-check is labelled as a review flag**, never as identity verification.
- **Transparent scoring.** The evidence summary is a checklist with reason codes, not a credit score.
- **Clear scope.** Documentation states what the system does not do, including liveness, government API lookup, forgery detection, and credit risk assessment.

### 4.4 For engineering and product teams

- **Honest architecture.** Streaming is app-level and documented as such. No claim of a vendor contract that does not exist.
- **Deterministic core.** Intake validation and extraction are deterministic and testable, with 69 backend tests covering validators, extraction, audio, documents, scorecard, and API/WebSocket/DB integration.
- **Clear extension points.** Adding a field means editing `REQUIRED_FIELDS`, `QUESTIONS`, `ANSWER_HINTS`, and `validate_field`. The frontend renders `extracted` generically.
- **Deployment-ready scaffolding.** Docker, Compose, nginx, Alembic, and CI are in place; the initial Alembic revision is committed and verified.

### 4.5 Quantified where we can

- Backend test suite: **69 tests, all passing** (intake validators, extraction, audio framing/normalisation, PAN/Aadhaar checks, document cross-checks, evidence summary, resumable uploads, streaming WebSocket, idempotent messages, field review, follow-ups, capabilities, metrics).
- Frontend production build: **1568 modules, 191.53 kB JS (59.53 kB gzipped)**, builds in ~2s.
- Live end-to-end verification: **80 checks, 0 failures**, including the full applicant journey, idempotency, documents, resumable audio, provider 503 paths, and storage cleanup.
- Eight required fields collected conversationally; maximum single recording 29 seconds (below Prisma's 30-second limit); streaming windows capped below it.

## 5. What the solution is not (scope limits)

To keep the scope honest, VoiceKYC deliberately does **not**:

- verify identity (no liveness, no government API lookup, no forgery or document-authenticity detection),
- assess creditworthiness or produce a credit score,
- approve or reject applicants autonomously,
- fabricate confidence scores,
- perform semantic LLM extraction (the deterministic extractor is the only path),
- provide a native vendor streaming contract (streaming is app-level),
- authenticate the agent dashboard (by design, for the prototype),
- encrypt data at rest or enforce a retention policy,
- run a background job queue (transcription and checks run inline).

These are documented as known limitations in `README.md` and `ARCHITECTURE.md`, not hidden.

## 6. Anything related to the solution

### 6.1 Configuration and dependencies

- **Gnani Speech API.** Prisma STT at `POST https://api.vachana.ai/stt/v3` (multipart `audio_file` + `language_code`), Timbre TTS at `POST https://api.vachana.ai/api/v1/tts/inference` (JSON with `model`, `voice`, `language`, `audio_config`), both authenticated with `X-API-Key-ID`. Only `GNANI_API_KEY` is normally required; base URL and paths have working defaults.
- **Optional tooling.** ffmpeg (non-WAV conversion, otherwise stored as-is), Tesseract (image OCR, otherwise applicant-supplied text). Both have honest fallbacks and are not required.
- **Settings loading.** Project-root `.env` and `backend/.env`; `backend/.env` wins when both define a key; process environment wins over both. Never commit a real key.

### 6.2 Data model

- `Application`: status, language, `extracted`, `reviewed_fields`, stored `assessment`, decision, decision note, timestamps.
- `ConversationMessage`: role, content, channel, `client_message_id`, created_at.
- `IdentityDocument`: doc type, filename, content type, size, storage path, extracted (masked) fields, cross-check result, extraction method.
- `AudioUpload`: status, byte offsets, duration, storage path, linked message.

### 6.3 Validation rules (syntax only, never truth)

- Names: letters and combining marks, at least 2 letters, max 80 chars.
- Dates of birth: DD/MM/YYYY, YYYY-MM-DD, DD Month YYYY, Devanagari digits; rejects future dates and pre-1900.
- Addresses: at least 10 characters.
- Occupations: at least 2 characters.
- Income: Indian units (k, lakh, crore), comma-grouped amounts, bounded 1..100,000,000.
- Loan purpose: at least 4 characters.
- Phone: Indian 10-digit mobile, normalising `+91`, `0091`, leading-zero forms.

### 6.4 Resilience and observability

- Provider calls retry transient network failures and `429`/`5xx` with exponential backoff.
- One pooled `httpx.AsyncClient` per lifespan.
- Every request gets an `X-Request-ID` and emits one JSON log line with method, path, status, duration. Provider calls log provider, status, latency — never credentials or transcript text.
- `GET /api/v1/metrics` returns process-local request/provider counters, application counts by status, uptime, and storage usage.

### 6.5 Testing and CI

- 69 backend tests using an isolated in-memory SQLite database and `tmp_path` storage; `get_db` overridden so tests never touch `backend/voicekyc.db`.
- CI runs `compileall`, `pytest`, `npm ci`, and `npm run build`.

### 6.6 Key files

- `backend/app/main.py` — wiring, lifespan, CORS, metrics middleware, error handler.
- `backend/app/api/routes/` — applications, documents, audio, voice, health, ops.
- `backend/app/services/` — intake, extraction, documents, scorecard, audio, ocr, storage, providers.
- `backend/app/models/application.py` — persistence entities.
- `backend/migrations/` — Alembic environment and initial revision.
- `frontend/src/components/UserInterface.jsx` — applicant interface.
- `frontend/src/components/AgentDashboard.jsx` — agent cockpit.
- `frontend/src/api/client.js` — all HTTP/upload/WebSocket calls.

## 7. Summary

VoiceKYC attacks the **collection** problem of KYC — getting structured, reviewable information from applicants who cannot or will not type English forms — by turning it into a **voice conversation in the applicant's own language**, while keeping every claim the system makes **honest and auditable**. It does not pretend to verify identity, score credit, or automate decisions. It collects statements, cross-checks documents as review flags, summarises evidence with reason codes, and hands a clean, evidence-backed record to a human who decides.

The guiding principle throughout: **a system that says less but means it is more useful than a system that says more and cannot back it up.**

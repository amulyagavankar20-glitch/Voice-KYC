import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.models.application import Application, ConversationMessage
from app.models.user import User
from app.schemas.application import (
    ApplicationDetail,
    ApplicationSummary,
    ChatMessageRequest,
    ChatResponse,
    ChatStartRequest,
    DecisionRequest,
    FieldReviewRequest,
    FollowUpRequest,
    MessageOut,
)
from app.services.extraction import clarification_attempt, local_candidates
from app.services.intake import (
    COMPLETION,
    QUESTIONS,
    REQUIRED_FIELDS,
    build_clarification_reply,
    build_follow_up,
    make_field_record,
    next_missing,
    progress,
    validate_field,
)
from app.services.scorecard import build_assessment

logger = logging.getLogger("voicekyc.applications")

router = APIRouter(prefix="/applications", tags=["applications"])

AI_PROVIDER = "deterministic"


def get_app_or_404(db: Session, app_id: str) -> Application:
    app = db.scalar(
        select(Application)
        .options(
            selectinload(Application.messages),
            selectinload(Application.documents),
            selectinload(Application.audio_uploads),
        )
        .where(Application.id == app_id)
    )
    if not app:
        raise HTTPException(status_code=404, detail="Application not found.")
    return app


def ensure_access(app: Application, user: User) -> None:
    """Strict per-user isolation: applicants access own apps; agents access review queue."""
    if user.is_agent:
        return
    if app.user_id and app.user_id == user.id:
        return
    # Unowned legacy/demo rows are not exposed to applicant accounts.
    raise HTTPException(status_code=404, detail="Application not found.")


def get_owned_app(db: Session, app_id: str, user: User) -> Application:
    app = get_app_or_404(db, app_id)
    ensure_access(app, user)
    return app


def add_message(
    db: Session,
    app: Application,
    role: str,
    content: str,
    channel: str = "text",
    client_message_id: str | None = None,
):
    app.messages.append(
        ConversationMessage(
            role=role,
            content=content,
            channel=channel,
            client_message_id=client_message_id,
        )
    )


def touch(app: Application) -> None:
    """Update the parent row even when only child messages changed."""
    app.updated_at = datetime.now(timezone.utc)


def documents_payload(app: Application) -> list[dict]:
    return [
        {"doc_type": document.doc_type, "cross_check": document.cross_check or {}}
        for document in app.documents
    ]


def audio_by_message(app: Application) -> dict[int, str]:
    return {
        upload.message_id: upload.id
        for upload in app.audio_uploads
        if upload.message_id is not None and upload.status == "complete"
    }


def transcript(app: Application) -> list[MessageOut]:
    audio_ids = audio_by_message(app)
    rows = []
    for message in app.messages:
        rows.append(
            MessageOut(
                id=message.id,
                role=message.role,
                content=message.content,
                channel=message.channel,
                audio_id=audio_ids.get(message.id),
                created_at=message.created_at,
            )
        )
    return rows


def chat_response(app: Application, reply: str, duplicate: bool = False) -> ChatResponse:
    return ChatResponse(
        application_id=app.id,
        reply=reply,
        extracted_data=app.extracted or {},
        progress=progress(app.extracted or {}),
        is_complete=app.status == "pending_review",
        status=app.status,
        current_field=app.current_field,
        ai_provider=AI_PROVIDER,
        duplicate=duplicate,
    )


@router.post("", response_model=ChatResponse, status_code=201)
def start_application(
    payload: ChatStartRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not payload.consent:
        raise HTTPException(status_code=400, detail="Applicant consent is required before collecting information.")
    app = Application(
        language=payload.language,
        status="in_progress",
        extracted={},
        current_field="full_name",
        user_id=user.id,
    )
    db.add(app)
    db.flush()
    intro = {
        "hinglish": (
            "Namaste! Main VoiceKYC microfinance intake assistant hoon. "
            "Main aapse loan application ke liye kuch zaroori KYC jaankari aur documents collect karunga. "
            "Yeh identity verification ya loan approval nahi hai; sabhi details agent review karega.\n\n"
        ),
        "marathi": (
            "नमस्कार! मी VoiceKYC मायक्रोफायनन्स इनटेक सहाय्यक आहे. "
            "कर्ज अर्जासाठी आवश्यक KYC माहिती आणि कागदपत्रे मी टप्प्याटप्प्याने घेईन. "
            "ही ओळख पडताळणी किंवा कर्ज मंजुरी नाही; सर्व माहिती प्रतिनिधी तपासेल.\n\n"
        ),
    }
    first_question = intro.get(payload.language, intro["hinglish"]) + QUESTIONS[payload.language]["full_name"]
    add_message(db, app, "assistant", first_question)
    touch(app)
    db.commit()
    logger.info(
        "application.started",
        extra={"event": "application.started", "application_id": app.id},
    )
    return chat_response(app, first_question)


@router.post("/{app_id}/messages", response_model=ChatResponse)
async def send_message(
    app_id: str,
    payload: ChatMessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app = get_owned_app(db, app_id, user)

    # Retry-safe: the same client message ID never produces a second user turn.
    if payload.client_message_id:
        existing = next(
            (message for message in app.messages if message.client_message_id == payload.client_message_id),
            None,
        )
        if existing is not None:
            last_reply = next(
                (message.content for message in reversed(app.messages) if message.role == "assistant"),
                "",
            )
            return chat_response(app, last_reply, duplicate=True)

    if app.status not in ("in_progress", "request_info"):
        raise HTTPException(status_code=409, detail="This application is not accepting additional responses.")
    if app.status == "request_info":
        app.status = "in_progress"
    add_message(db, app, "user", payload.message, channel="text", client_message_id=payload.client_message_id)

    pending_before = [f for f in REQUIRED_FIELDS if not (app.extracted or {}).get(f, {}).get("value")]
    if not pending_before:
        app.status = "pending_review"
        app.current_field = None
        reply = COMPLETION[app.language]
    else:
        primary = pending_before[0]
        attempt = clarification_attempt(app.messages, app.language, primary)

        extracted = dict(app.extracted or {})
        # Per-field candidates: field -> (value, source, evidence, raw_span)
        candidates: dict[str, tuple[str, str, str | None, str | None]] = {}

        # 1) Deterministic rules fallback (current primary field only).
        rules_value = None
        rules_error = None
        for candidate_text in local_candidates(primary, payload.message):
            rules_value, rules_error = validate_field(primary, candidate_text)
            if rules_value is not None:
                break
        if rules_value is not None:
            rules_evidence = payload.message if rules_value != payload.message.strip() else None
            candidates[primary] = (rules_value, "rules", rules_evidence, None)

        # 2) Optional LLM extraction (Groq GPT-OSS) over the full transcript.
        llm_clarification: str | None = None
        if settings.llm_configured:
            from app.services.llm_extraction import extract_fields_llm

            llm_result = await extract_fields_llm(
                app.messages,
                app.language,
                pending_before,
                current_question=QUESTIONS[app.language].get(primary),
            )
            if llm_result:
                llm_clarification = llm_result.get("clarification_question")
                for field_key in pending_before:
                    entry = (llm_result.get("fields") or {}).get(field_key, {})
                    raw_val = entry.get("value")
                    if raw_val is None:
                        continue
                    validated, _err = validate_field(field_key, raw_val)
                    if validated is None:
                        continue
                    raw_span = entry.get("raw_span")
                    evidence = raw_span if raw_span else (payload.message if validated != payload.message.strip() else None)
                    # LLM takes precedence over rules for the same field.
                    candidates[field_key] = (validated, "llm", evidence, raw_span)

        # 3) Apply validated candidates (LLM-preferred) to the record.
        filled_any = False
        for field_key, (value, source, evidence, raw_span) in candidates.items():
            extracted[field_key] = make_field_record(
                value,
                evidence=evidence,
                extraction_source=source,
                raw_span=raw_span,
            )
            filled_any = True

        if filled_any:
            app.extracted = extracted
            next_field = next_missing(app.extracted)
            if next_field:
                app.current_field = next_field
                reply = QUESTIONS[app.language][next_field]
            else:
                app.current_field = None
                app.status = "pending_review"
                reply = COMPLETION[app.language]
        else:
            # Nothing validated: clarify. Prefer a short LLM question if provided.
            if llm_clarification and len(llm_clarification) <= 400:
                reply = llm_clarification
            else:
                validation_error = rules_error
                reply = build_clarification_reply(app.language, primary, validation_error, attempt)

    add_message(db, app, "assistant", reply)
    touch(app)
    db.commit()
    logger.info(
        "application.message_processed",
        extra={
            "event": "application.message_processed",
            "application_id": app.id,
            "status_code": 200,
        },
    )
    return chat_response(app, reply)


@router.get("", response_model=list[ApplicationSummary])
def list_applications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Application).order_by(Application.created_at.desc())
    # Strict isolation: applicants see only own applications; agents see review queue.
    if not user.is_agent:
        query = query.where(Application.user_id == user.id)
    apps = db.scalars(query).all()
    result = []
    for app in apps:
        extracted = app.extracted or {}
        assessed = [item["confidence"] for item in extracted.values() if item.get("confidence") is not None]
        stored = app.assessment or {}
        result.append(
            ApplicationSummary(
                id=app.id,
                name=extracted.get("full_name", {}).get("value", "Applicant"),
                status=app.status,
                language=app.language,
                created_at=app.created_at,
                updated_at=app.updated_at,
                progress=progress(extracted),
                confidence_avg=round(sum(assessed) / len(assessed), 2) if assessed else None,
                review_priority=stored.get("recommendation"),
                evidence_score=stored.get("score"),
            )
        )
    return result


@router.get("/{app_id}", response_model=ApplicationDetail)
def get_application(
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app = get_owned_app(db, app_id, user)
    assessment = build_assessment(app.extracted or {}, documents_payload(app))
    return ApplicationDetail(
        id=app.id,
        status=app.status,
        language=app.language,
        created_at=app.created_at,
        updated_at=app.updated_at,
        extracted=app.extracted or {},
        reviewed_fields=app.reviewed_fields or {},
        assessment=assessment,
        conversation=transcript(app),
        decision=app.decision,
        decision_note=app.decision_note,
    )


@router.get("/{app_id}/assessment")
def get_assessment(
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app = get_owned_app(db, app_id, user)
    assessment = build_assessment(app.extracted or {}, documents_payload(app))
    app.assessment = assessment
    touch(app)
    db.commit()
    return assessment


@router.post("/{app_id}/fields/{field}/review")
def review_field(
    app_id: str,
    field: str,
    payload: FieldReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.is_agent:
        raise HTTPException(status_code=403, detail="Only review agents can record field reviews.")
    if field not in REQUIRED_FIELDS:
        raise HTTPException(status_code=404, detail="Unknown intake field.")
    app = get_owned_app(db, app_id, user)
    if field not in (app.extracted or {}):
        raise HTTPException(status_code=409, detail="That field has no applicant value to review.")
    reviewed = dict(app.reviewed_fields or {})
    reviewed[field] = {
        "status": payload.status,
        "note": payload.note,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    app.reviewed_fields = reviewed
    touch(app)
    db.commit()
    logger.info(
        "application.field_reviewed",
        extra={"event": "application.field_reviewed", "application_id": app.id, "field": field},
    )
    return {
        "message": "Field review recorded.",
        "field": field,
        "review": reviewed[field],
        # The applicant record intentionally keeps needs_review: true.
        "applicant_field_unchanged": True,
    }


@router.post("/{app_id}/follow-up")
def follow_up(
    app_id: str,
    payload: FollowUpRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.is_agent:
        raise HTTPException(status_code=403, detail="Only review agents can send follow-ups.")
    app = get_owned_app(db, app_id, user)
    field = payload.field or next_missing(app.extracted or {}) or "full_name"
    if field not in REQUIRED_FIELDS:
        raise HTTPException(status_code=422, detail="Unknown intake field.")
    mismatch = next(
        (
            document.cross_check.get("mismatches", [])
            for document in app.documents
            if (document.cross_check or {}).get("overall") == "inconsistent"
        ),
        [],
    )
    question = build_follow_up(app.language, field, mismatch=mismatch or None)
    sent = False
    if payload.send:
        if app.status not in ("in_progress", "request_info", "pending_review"):
            raise HTTPException(status_code=409, detail="This application is closed.")
        add_message(db, app, "assistant", question, channel="follow_up")
        app.status = "request_info"
        app.current_field = field
        touch(app)
        db.commit()
        sent = True
        logger.info(
            "application.follow_up_sent",
            extra={"event": "application.follow_up_sent", "application_id": app.id, "field": field},
        )
    return {"question": question, "field": field, "language": app.language, "sent": sent}


@router.post("/{app_id}/decision")
def make_decision(
    app_id: str,
    payload: DecisionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.is_agent:
        raise HTTPException(status_code=403, detail="Only review agents can record decisions.")
    app = get_owned_app(db, app_id, user)
    if app.status not in ("pending_review", "in_progress"):
        raise HTTPException(status_code=409, detail="A decision has already been recorded or the application is not reviewable.")
    if payload.decision == "approve" and next_missing(app.extracted or {}):
        raise HTTPException(status_code=409, detail="Cannot approve an application with missing required information.")
    app.decision = payload.decision
    app.decision_note = payload.note
    app.status = payload.decision
    touch(app)
    db.commit()
    logger.info(
        "application.decision_recorded",
        extra={"event": "application.decision_recorded", "application_id": app.id},
    )
    return {"message": "Human review decision recorded.", "status": app.status}


@router.delete("/{app_id}", status_code=204)
def delete_application(
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services import storage

    app = get_owned_app(db, app_id, user)
    for document in app.documents:
        storage.delete(document.storage_path)
    for upload in app.audio_uploads:
        storage.delete(upload.storage_path)
    db.delete(app)
    db.commit()
    logger.info(
        "application.deleted",
        extra={"event": "application.deleted", "application_id": app_id},
    )
    return None

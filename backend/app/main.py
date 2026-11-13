import logging
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import models
from app.api.routes import applications, audio, auth, documents, health, ops, voice
from app.core.config import settings
from app.core.http import set_shared_client
from app.core.logging import configure_logging, request_id_ctx
from app.core.metrics import metrics
from app.db.base import Base
from app.db.migrate import apply_startup_migrations
from app.db.session import SessionLocal, engine

_ = models  # imported for its side effect: registers ORM models with Base.metadata

configure_logging(settings.log_level)
logger = logging.getLogger("voicekyc.api")

API_VERSION = "2.1.0"


def _seed_demo_application() -> None:
    """Create one synthetic, fully-collected application for agent demos.

    Only runs when ``SEED_DEMO_APPLICATION`` is true and the database is empty,
    so it never duplicates records or touches real applicant data.
    """
    from datetime import datetime, timezone

    from app.models.application import Application, ConversationMessage
    from app.services.intake import COMPLETION, QUESTIONS, make_field_record

    db = SessionLocal()
    try:
        if db.query(Application).count() > 0:
            return
        extracted = {
            "full_name": make_field_record("Ramesh Kumar", extraction_source="rules"),
            "father_name": make_field_record("Suresh Kumar", extraction_source="rules"),
            "date_of_birth": make_field_record("12/03/1990", extraction_source="rules"),
            "address": make_field_record("House 12, MG Road, Pune, Maharashtra", extraction_source="rules"),
            "occupation": make_field_record("farmer", extraction_source="rules"),
            "monthly_income": make_field_record("15000", extraction_source="rules"),
            "loan_purpose": make_field_record("beej aur khad kharidne ke liye", extraction_source="rules"),
            "phone": make_field_record("9876543210", extraction_source="rules"),
        }
        app = Application(
            language="hinglish",
            status="pending_review",
            extracted=extracted,
            current_field=None,
            updated_at=datetime.now(timezone.utc),
        )
        db.add(app)
        db.flush()
        db.add(ConversationMessage(application_id=app.id, role="assistant", content=QUESTIONS["hinglish"]["full_name"], channel="text"))
        db.add(ConversationMessage(application_id=app.id, role="user", content="Ramesh Kumar", channel="text"))
        db.add(ConversationMessage(application_id=app.id, role="assistant", content=COMPLETION["hinglish"], channel="text"))
        db.commit()
        logger.info("application.demo_seeded", extra={"event": "application.demo_seeded", "application_id": app.id})
    except Exception as exc:  # pragma: no cover - defensive
        db.rollback()
        logger.warning("application.demo_seed_failed", extra={"event": "application.demo_seed_failed", "error": str(exc)})
    finally:
        db.close()


def _seed_demo_users() -> None:
    """Seed a few synthetic user accounts for auth testing (fake data only)."""
    from app.models.user import User
    from app.services.auth import hash_password

    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return
        demo_users = [
            ("ramesh.kumar", "Ramesh Kumar", "ramesh.kumar@example.com", False),
            ("suresh.patil", "Suresh Patil", "suresh.patil@example.com", False),
            ("anita.sharma", "Anita Sharma", "anita.sharma@example.com", False),
            ("vikram.singh", "Vikram Singh", "vikram.singh@example.com", False),
            ("priya.desai", "Priya Desai", "priya.desai@example.com", False),
            ("agent.review", "Review Agent", "agent.review@example.com", True),
        ]
        for username, full_name, email, is_agent in demo_users:
            db.add(
                User(
                    username=username,
                    password_hash=hash_password("Demo@1234"),
                    full_name=full_name,
                    email=email,
                    is_agent=is_agent,
                )
            )
        db.commit()
        logger.info("auth.demo_users_seeded", extra={"event": "auth.demo_users_seeded", "count": len(demo_users)})
    except Exception as exc:  # pragma: no cover - defensive
        db.rollback()
        logger.warning("auth.demo_users_seed_failed", extra={"event": "auth.demo_users_seed_failed", "error": str(exc)})
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    apply_startup_migrations(engine)
    if settings.seed_demo_application:
        _seed_demo_application()
    if settings.seed_demo_users:
        _seed_demo_users()
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.gnani_request_timeout_seconds),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    )
    set_shared_client(client)
    logger.info("application.startup", extra={"event": "application.startup"})
    try:
        yield
    finally:
        set_shared_client(None)
        await client.aclose()
        engine.dispose()
        logger.info("application.shutdown", extra={"event": "application.shutdown"})


app = FastAPI(
    title=settings.app_name,
    version=API_VERSION,
    description="Voice-first assisted KYC workflow API",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a correlation ID to the request, logs, and response."""
    incoming = request.headers.get("X-Request-ID")
    request_id = incoming if incoming and len(incoming) <= 128 else str(uuid.uuid4())
    request.state.request_id = request_id
    token = request_id_ctx.set(request_id)
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        metrics.record_request(request.method, request.url.path, status_code, duration_ms)
        logger.info(
            "request.completed",
            extra={
                "event": "request.completed",
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
        )
        request_id_ctx.reset(token)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "request.unhandled_exception",
        extra={"event": "request.unhandled_exception", "path": request.url.path},
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error.",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


app.include_router(health.router, prefix=settings.api_v1_prefix)
app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(ops.router, prefix=settings.api_v1_prefix)
app.include_router(applications.router, prefix=settings.api_v1_prefix)
app.include_router(documents.router, prefix=settings.api_v1_prefix)
app.include_router(audio.router, prefix=settings.api_v1_prefix)
app.include_router(voice.router, prefix=settings.api_v1_prefix)


@app.get("/")
def root():
    return {
        "service": settings.app_name,
        "version": API_VERSION,
        "health": f"{settings.api_v1_prefix}/health",
        "docs": "/docs",
    }

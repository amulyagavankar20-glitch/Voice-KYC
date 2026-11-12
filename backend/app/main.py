import logging
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import models
from app.api.routes import applications, audio, documents, health, ops, voice
from app.core.config import settings
from app.core.http import set_shared_client
from app.core.logging import configure_logging, request_id_ctx
from app.core.metrics import metrics
from app.db.base import Base
from app.db.migrate import apply_startup_migrations
from app.db.session import engine

_ = models  # imported for its side effect: registers ORM models with Base.metadata

configure_logging(settings.log_level)
logger = logging.getLogger("voicekyc.api")

API_VERSION = "2.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    apply_startup_migrations(engine)
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

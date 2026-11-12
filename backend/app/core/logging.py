"""Structured JSON logging with request-scoped correlation IDs."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# Set by the request-context middleware so every log line emitted while handling
# a request can be correlated with the response's X-Request-ID header.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

_LOG_EXTRA_FIELDS = (
    "event",
    "provider",
    "duration_ms",
    "status_code",
    "application_id",
    "path",
    "method",
    "attempt",
    "retryable",
    "field",
    "needs_clarification",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id
        for key in _LOG_EXTRA_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn installs its own handlers; clear them so output stays one-line JSON.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # One request log line per call is enough; httpx's own INFO line is noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)

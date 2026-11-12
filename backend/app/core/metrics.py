"""Small in-process metrics registry for provider and request telemetry.

This is intentionally dependency-free and single-process. It exposes the
numbers the agent/ops floor needs (provider calls, error rates, latency) without
pretending to be a production APM. OTel/Prometheus can read the same snapshot.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self._requests: dict[str, int] = defaultdict(int)
        self._request_errors: dict[str, int] = defaultdict(int)
        self._providers: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"calls": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0, "last_status": 0}
        )
        self._events: dict[str, int] = defaultdict(int)

    def record_request(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        key = f"{method} {path}"
        with self._lock:
            self._requests[key] += 1
            if status_code >= 500:
                self._request_errors[key] += 1
            bucket = self._providers.setdefault(
                "http", {"calls": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0, "last_status": 0}
            )
            bucket["calls"] += 1
            bucket["total_ms"] += duration_ms
            bucket["max_ms"] = max(float(bucket["max_ms"]), duration_ms)

    def record_provider(
        self,
        provider: str,
        *,
        duration_ms: float,
        status_code: int | None = None,
        error: bool = False,
    ) -> None:
        with self._lock:
            bucket = self._providers[provider]
            bucket["calls"] += 1
            bucket["total_ms"] += duration_ms
            bucket["max_ms"] = max(float(bucket["max_ms"]), duration_ms)
            bucket["last_status"] = status_code or 0
            if error or (status_code is not None and status_code >= 400):
                bucket["errors"] += 1

    def count(self, event: str, amount: int = 1) -> None:
        with self._lock:
            self._events[event] += amount

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            providers = {
                name: {
                    "calls": int(value["calls"]),
                    "errors": int(value["errors"]),
                    "error_rate": round(int(value["errors"]) / max(1, int(value["calls"])), 3),
                    "avg_ms": round(float(value["total_ms"]) / max(1, int(value["calls"])), 2),
                    "max_ms": round(float(value["max_ms"]), 2),
                    "last_status": int(value["last_status"]),
                }
                for name, value in self._providers.items()
            }
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "requests": dict(self._requests),
                "request_errors": dict(self._request_errors),
                "providers": providers,
                "events": dict(self._events),
            }

    def reset(self) -> None:
        with self._lock:
            self.started_at = time.time()
            self._requests.clear()
            self._request_errors.clear()
            self._providers.clear()
            self._events.clear()


metrics = MetricsRegistry()

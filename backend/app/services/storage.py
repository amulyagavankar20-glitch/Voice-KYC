"""Local filesystem storage for uploaded audio and identity documents.

Files are written outside the source tree (``STORAGE_DIR``) with generated
names. The database stores a relative path, never an absolute one.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import settings


def root() -> Path:
    path = settings.storage_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
        return ".bin"
    return suffix


def allocate_path(filename: str | None, *, prefix: str = "file") -> str:
    """Return a fresh relative path without writing a file."""
    return f"{prefix}-{uuid.uuid4().hex}{_safe_suffix(filename)}"


def save_bytes(data: bytes, filename: str | None, *, prefix: str = "file") -> str:
    """Persist bytes and return a relative path using forward slashes."""
    if not data:
        raise ValueError("Refusing to store an empty file.")
    relative = allocate_path(filename, prefix=prefix)
    destination = root() / relative
    destination.write_bytes(data)
    return relative


def resolve(relative_path: str) -> Path:
    """Resolve a stored path, refusing traversal outside the storage root."""
    base = root()
    candidate = (base / relative_path).resolve()
    if base != candidate and base not in candidate.parents:
        raise ValueError("Stored path escapes the storage directory.")
    return candidate


def read_bytes(relative_path: str) -> bytes:
    return resolve(relative_path).read_bytes()


def append_bytes(relative_path: str, data: bytes) -> None:
    with resolve(relative_path).open("ab") as handle:
        handle.write(data)


def replace_bytes(relative_path: str, data: bytes) -> None:
    resolve(relative_path).write_bytes(data)


def delete(relative_path: str | None) -> None:
    if not relative_path:
        return
    try:
        resolve(relative_path).unlink(missing_ok=True)
    except (OSError, ValueError):
        # Cleanup must never fail the request that triggered it.
        pass


def usage_bytes() -> int:
    total = 0
    base = root()
    for path in base.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total

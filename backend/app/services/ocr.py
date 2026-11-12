"""Optional OCR adapter.

Tesseract is not a Python dependency of this project. If the executable exists
on the machine we use it for image documents; otherwise the API accepts
applicant-supplied document text and labels the extraction method honestly.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import settings


def tesseract_available() -> bool:
    return shutil.which(settings.tesseract_cmd) is not None


def ocr_image(content: bytes, filename: str | None = None) -> str | None:
    binary = shutil.which(settings.tesseract_cmd)
    if not binary:
        return None
    suffix = Path(filename or "document.png").suffix or ".png"
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / f"input{suffix}"
        source.write_bytes(content)
        process = subprocess.run(
            [binary, str(source), "stdout", "-l", "eng+hin+mar"],
            capture_output=True,
            timeout=45,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if process.returncode != 0:
            return None
        text = process.stdout.strip()
        return text or None

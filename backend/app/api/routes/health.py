from fastapi import APIRouter

from app.core.config import settings
from app.services.ocr import tesseract_available

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name, "environment": settings.app_env}


@router.get("/capabilities")
def capabilities():
    return {
        "text_intake": True,
        "deterministic_extraction": True,
        "prisma_configured": settings.prisma_configured,
        "timbre_configured": settings.timbre_configured,
        "llm_provider": settings.llm_provider,
        "llm_configured": settings.llm_configured,
        "streaming_stt": settings.prisma_configured,
        "document_ocr": tesseract_available(),
        "document_text_fallback": True,
        "scorecard": True,
        "resumable_uploads": True,
        "note": "A capability is enabled only when its dependencies are present.",
    }

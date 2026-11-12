import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.routes.applications import get_app_or_404
from app.core.config import settings
from app.db.session import get_db
from app.models.application import AudioUpload, ConversationMessage
from app.schemas.application import AudioOut
from app.services import storage
from app.services.audio import compress_audio

logger = logging.getLogger("voicekyc.audio")

router = APIRouter(prefix="/applications", tags=["audio"])


def _message_belongs(db: Session, app_id: str, message_id: int | None) -> None:
    if message_id is None:
        return
    message = db.get(ConversationMessage, message_id)
    if not message or message.application_id != app_id:
        raise HTTPException(status_code=422, detail="message_id does not belong to this application.")


def _stored_name(filename: str | None, content_type: str) -> str:
    if content_type == "audio/wav":
        return "recording.wav"
    return Path(filename or "recording.bin").name


def _finalise(upload: AudioUpload, data: bytes, filename: str | None) -> None:
    audio, content_type, duration_ms, method = compress_audio(
        data,
        content_type=upload.content_type,
        filename=filename,
    )
    final_name = _stored_name(filename, content_type)
    final_path = storage.save_bytes(audio, final_name, prefix=f"audio-{upload.id[:8]}")
    storage.delete(upload.storage_path)
    upload.storage_path = final_path
    upload.content_type = content_type
    upload.size_bytes = len(audio)
    upload.duration_ms = duration_ms
    upload.status = "complete"
    upload.received_bytes = len(audio)
    upload.filename = final_name
    logger.info(
        "application.audio_stored",
        extra={
            "event": "application.audio_stored",
            "application_id": upload.application_id,
            "provider": method,
        },
    )


@router.post("/{app_id}/audio", response_model=AudioOut, status_code=201)
async def upload_audio(
    app_id: str,
    file: UploadFile = File(...),
    message_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
):
    app = get_app_or_404(db, app_id)
    _message_belongs(db, app_id, message_id)
    content = await file.read(settings.max_audio_bytes + 1)
    if len(content) > settings.max_audio_bytes:
        raise HTTPException(status_code=413, detail="Audio exceeds the configured upload limit.")
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded audio is empty.")

    upload = AudioUpload(
        application_id=app.id,
        message_id=message_id,
        filename=file.filename,
        content_type=(file.content_type or "application/octet-stream").split(";")[0],
        status="uploading",
    )
    db.add(upload)
    db.flush()
    try:
        _finalise(upload, content, file.filename)
        db.commit()
        db.refresh(upload)
    except Exception:
        storage.delete(upload.storage_path)
        raise
    return upload


@router.post("/{app_id}/audio/init", response_model=AudioOut, status_code=201)
def init_audio(
    app_id: str,
    filename: str = Form(default="recording.webm"),
    content_type: str = Form(default="application/octet-stream"),
    message_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Start a resumable upload. Chunks are appended with explicit offsets."""
    app = get_app_or_404(db, app_id)
    _message_belongs(db, app_id, message_id)
    upload = AudioUpload(
        application_id=app.id,
        message_id=message_id,
        filename=Path(filename).name,
        content_type=content_type.split(";")[0],
        status="uploading",
        received_bytes=0,
    )
    db.add(upload)
    db.flush()
    upload.storage_path = storage.allocate_path(filename, prefix=f"part-{upload.id[:8]}")
    storage.resolve(upload.storage_path).write_bytes(b"")
    db.commit()
    db.refresh(upload)
    logger.info(
        "application.audio_upload_started",
        extra={"event": "application.audio_upload_started", "application_id": app.id},
    )
    return upload


@router.post("/{app_id}/audio/{upload_id}/chunk", response_model=AudioOut)
async def upload_chunk(
    app_id: str,
    upload_id: str,
    offset: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    app = get_app_or_404(db, app_id)
    upload = db.get(AudioUpload, upload_id)
    if not upload or upload.application_id != app.id:
        raise HTTPException(status_code=404, detail="Audio upload not found.")
    if upload.status != "uploading":
        raise HTTPException(status_code=409, detail="This upload is already complete.")
    if offset != upload.received_bytes:
        raise HTTPException(
            status_code=409,
            detail={"message": "Unexpected chunk offset.", "expected_offset": upload.received_bytes},
        )
    chunk = await file.read(settings.max_audio_bytes + 1)
    if not chunk:
        raise HTTPException(status_code=400, detail="Empty chunk.")
    if upload.received_bytes + len(chunk) > settings.max_audio_bytes:
        raise HTTPException(status_code=413, detail="Audio exceeds the configured upload limit.")
    if upload.storage_path:
        storage.append_bytes(upload.storage_path, chunk)
    upload.received_bytes += len(chunk)
    db.commit()
    db.refresh(upload)
    return upload


@router.post("/{app_id}/audio/{upload_id}/complete", response_model=AudioOut)
def complete_upload(app_id: str, upload_id: str, db: Session = Depends(get_db)):
    app = get_app_or_404(db, app_id)
    upload = db.get(AudioUpload, upload_id)
    if not upload or upload.application_id != app.id:
        raise HTTPException(status_code=404, detail="Audio upload not found.")
    if upload.status == "complete":
        return upload
    if not upload.storage_path:
        raise HTTPException(status_code=409, detail="Upload has no data.")
    data = storage.read_bytes(upload.storage_path)
    if not data:
        raise HTTPException(status_code=400, detail="No audio was uploaded.")
    _finalise(upload, data, upload.filename)
    db.commit()
    db.refresh(upload)
    return upload


@router.get("/{app_id}/audio", response_model=list[AudioOut])
def list_audio(app_id: str, db: Session = Depends(get_db)):
    app = get_app_or_404(db, app_id)
    return app.audio_uploads


@router.get("/{app_id}/audio/{upload_id}")
def get_audio(app_id: str, upload_id: str, db: Session = Depends(get_db)):
    upload = db.get(AudioUpload, upload_id)
    if not upload or upload.application_id != app_id or upload.status != "complete" or not upload.storage_path:
        raise HTTPException(status_code=404, detail="Audio not found.")
    path = storage.resolve(upload.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file is missing from storage.")
    return FileResponse(
        path,
        media_type=upload.content_type or "application/octet-stream",
        filename=upload.filename or path.name,
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/{app_id}/audio/{upload_id}", status_code=204)
def delete_audio(app_id: str, upload_id: str, db: Session = Depends(get_db)):
    upload = db.get(AudioUpload, upload_id)
    if not upload or upload.application_id != app_id:
        raise HTTPException(status_code=404, detail="Audio not found.")
    storage.delete(upload.storage_path)
    db.delete(upload)
    db.commit()
    return None

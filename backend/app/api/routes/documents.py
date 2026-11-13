import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.routes.applications import get_owned_app, touch
from app.api.routes.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.application import IdentityDocument
from app.models.user import User
from app.schemas.application import DocumentOut
from app.services import ocr, storage
from app.services.documents import DOC_TYPES, cross_check, extract_fields

logger = logging.getLogger("voicekyc.documents")

router = APIRouter(prefix="/applications", tags=["documents"])

TEXT_CONTENT_TYPES = {"text/plain", "text/csv"}


def media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _document_text(text: str | None, file: UploadFile | None, content: bytes) -> tuple[str, str]:
    if text and text.strip():
        return text.strip(), "applicant_supplied_text"
    if not file:
        raise HTTPException(
            status_code=422,
            detail="Provide the document text or upload an image that OCR can read.",
        )
    if media_type(file.content_type) in TEXT_CONTENT_TYPES:
        return content.decode("utf-8", errors="replace").strip(), "plain_text_file"
    extracted = ocr.ocr_image(content, file.filename)
    if not extracted:
        raise HTTPException(
            status_code=422,
            detail=(
                "OCR is not available or found no text. Install Tesseract or send the "
                "document text in the `text` form field."
            ),
        )
    return extracted, "ocr_tesseract"


@router.post("/{app_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    app_id: str,
    doc_type: str = Form(...),
    text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if doc_type not in DOC_TYPES:
        raise HTTPException(status_code=422, detail="doc_type must be 'pan' or 'aadhaar'.")
    app = get_owned_app(db, app_id, user)

    content = b""
    if file is not None:
        content = await file.read(settings.max_document_bytes + 1)
        if len(content) > settings.max_document_bytes:
            raise HTTPException(status_code=413, detail="Document exceeds the configured upload limit.")
        if not content:
            raise HTTPException(status_code=400, detail="The uploaded document is empty.")

    document_text, method = _document_text(text, file, content)
    fields = extract_fields(document_text, doc_type)
    if "document_number" not in fields:
        raise HTTPException(
            status_code=422,
            detail=f"No valid {doc_type.upper()} number was found in the supplied document.",
        )
    if fields.get("number_valid") is False:
        raise HTTPException(
            status_code=422,
            detail=f"The {doc_type.upper()} number found failed its checksum/format check.",
        )

    check = cross_check(app.extracted or {}, fields)
    stored_path = None
    try:
        if content:
            stored_path = storage.save_bytes(content, file.filename if file else None, prefix=f"doc-{doc_type}")
        document = IdentityDocument(
            application_id=app.id,
            doc_type=doc_type,
            filename=file.filename if file else None,
            content_type=file.content_type if file else "text/plain",
            size_bytes=len(content),
            storage_path=stored_path,
            extracted_fields=fields,
            cross_check=check,
            extraction_method=method,
        )
        db.add(document)
        touch(app)
        db.commit()
        db.refresh(document)
    except Exception:
        storage.delete(stored_path)
        raise

    logger.info(
        "application.document_uploaded",
        extra={
            "event": "application.document_uploaded",
            "application_id": app.id,
            "provider": "ocr" if method == "ocr_tesseract" else "local",
        },
    )
    return document


@router.get("/{app_id}/documents", response_model=list[DocumentOut])
def list_documents(
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app = get_owned_app(db, app_id, user)
    return app.documents


@router.delete("/{app_id}/documents/{document_id}", status_code=204)
def delete_document(
    app_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app = get_owned_app(db, app_id, user)
    document = next((row for row in app.documents if row.id == document_id), None)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    storage.delete(document.storage_path)
    db.delete(document)
    touch(app)
    db.commit()
    return None

"""Authentication routes: register, login, current user, document upload."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User, UserDocument
from app.services import storage
from app.services.auth import (
    authenticate_user,
    create_access_token,
    decode_access_token,
    get_user_by_id,
    hash_password,
    validate_password,
    validate_username,
)

logger = logging.getLogger("voicekyc.auth")

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=2, max_length=120)
    email: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict[str, Any]


class UserOut(BaseModel):
    id: str
    username: str
    full_name: str
    email: str | None = None
    is_active: bool


def _user_out(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "is_active": user.is_active,
    }


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    payload = decode_access_token(credentials.credentials)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")
    user = get_user_by_id(db, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive.")
    return user


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    err = validate_username(payload.username)
    if err:
        raise HTTPException(status_code=422, detail=err)
    err = validate_password(payload.password)
    if err:
        raise HTTPException(status_code=422, detail=err)
    if payload.email and "@" not in payload.email:
        raise HTTPException(status_code=422, detail="Invalid email address.")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Username already taken.")
    user = User(
        username=payload.username.strip(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        email=payload.email,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("auth.registered", extra={"event": "auth.registered", "user_id": user.id})
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": _user_out(user)}


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
    logger.info("auth.login", extra={"event": "auth.login", "user_id": user.id})
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": _user_out(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_out(user)


@router.post("/documents", status_code=201)
async def upload_document(
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if doc_type not in {"pan", "aadhaar", "passport", "voter_id", "other"}:
        raise HTTPException(status_code=422, detail="Unsupported document type.")
    content = await file.read(settings.max_document_bytes + 1)
    if len(content) > settings.max_document_bytes:
        raise HTTPException(status_code=413, detail="Document exceeds the configured upload limit.")
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded document is empty.")
    stored_path = storage.save_bytes(content, file.filename, prefix=f"userdoc-{user.id[:8]}")
    doc = UserDocument(
        user_id=user.id,
        doc_type=doc_type,
        filename=file.filename,
        content_type=(file.content_type or "application/octet-stream").split(";")[0],
        size_bytes=len(content),
        storage_path=stored_path,
        verification_status="uploaded",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    logger.info("auth.document_uploaded", extra={"event": "auth.document_uploaded", "user_id": user.id})
    return {
        "id": doc.id,
        "doc_type": doc.doc_type,
        "filename": doc.filename,
        "size_bytes": doc.size_bytes,
        "verification_status": doc.verification_status,
        "created_at": doc.created_at,
    }


@router.get("/documents")
def list_documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    docs = db.query(UserDocument).filter(UserDocument.user_id == user.id).all()
    return [
        {
            "id": d.id,
            "doc_type": d.doc_type,
            "filename": d.filename,
            "size_bytes": d.size_bytes,
            "verification_status": d.verification_status,
            "created_at": d.created_at,
        }
        for d in docs
    ]


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.query(UserDocument).filter(UserDocument.id == document_id, UserDocument.user_id == user.id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    storage.delete(doc.storage_path)
    db.delete(doc)
    db.commit()
    return None

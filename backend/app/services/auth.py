"""Authentication helpers: password hashing, JWT tokens, user lookup."""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 8
PASSWORD_MIN_LENGTH = 8


def _jwt_secret() -> str:
    # Prefer an explicit secret; fall back to the app secret key. In production
    # set SECRET_KEY to a long random value (already documented).
    return os.environ.get("JWT_SECRET") or settings.secret_key


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate_password(password: str) -> str | None:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain at least one letter and one number."
    return None


def validate_username(username: str) -> str | None:
    if not isinstance(username, str):
        return "Username is required."
    username = username.strip()
    if len(username) < 3 or len(username) > 64:
        return "Username must be 3-64 characters."
    if not re.fullmatch(r"[A-Za-z0-9._-]+", username):
        return "Username may only contain letters, digits, dots, underscores, and hyphens."
    return None


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "username": user.username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user

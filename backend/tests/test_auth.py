import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "storage_dir", str(tmp_path))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


def test_register_login_me(client):
    r = client.post(
        "/api/v1/auth/register",
        json={"username": "test.user", "password": "Passw0rd!", "full_name": "Test User"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["token_type"] == "bearer"
    assert data["user"]["username"] == "test.user"
    token = data["access_token"]

    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["full_name"] == "Test User"

    r = client.post("/api/v1/auth/login", json={"username": "test.user", "password": "Passw0rd!"})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_login_invalid_credentials(client):
    client.post(
        "/api/v1/auth/register",
        json={"username": "u2", "password": "Passw0rd!", "full_name": "U2"},
    )
    r = client.post("/api/v1/auth/login", json={"username": "u2", "password": "wrong"})
    assert r.status_code == 401
    r = client.post("/api/v1/auth/login", json={"username": "nope", "password": "Passw0rd!"})
    assert r.status_code == 401


def test_register_duplicate_username(client):
    client.post("/api/v1/auth/register", json={"username": "dup", "password": "Passw0rd!", "full_name": "Dup"})
    r = client.post("/api/v1/auth/register", json={"username": "dup", "password": "Passw0rd!", "full_name": "Dup2"})
    assert r.status_code == 409


def test_register_weak_password(client):
    r = client.post("/api/v1/auth/register", json={"username": "weak", "password": "short", "full_name": "Weak"})
    assert r.status_code == 422


def test_me_requires_auth(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_document_upload_requires_auth_and_lists(client):
    r = client.post(
        "/api/v1/auth/register",
        json={"username": "docuser", "password": "Passw0rd!", "full_name": "Doc User"},
    )
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # unauthenticated upload rejected
    r = client.post("/api/v1/auth/documents", data={"doc_type": "pan"})
    assert r.status_code == 401

    # authenticated upload
    files = {"file": ("id.txt", io.BytesIO(b"hello world"), "text/plain")}
    r = client.post("/api/v1/auth/documents", data={"doc_type": "pan"}, files=files, headers=headers)
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]
    assert r.json()["verification_status"] == "uploaded"

    r = client.get("/api/v1/auth/documents", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.delete(f"/api/v1/auth/documents/{doc_id}", headers=headers)
    assert r.status_code == 204
    r = client.get("/api/v1/auth/documents", headers=headers)
    assert r.json() == []
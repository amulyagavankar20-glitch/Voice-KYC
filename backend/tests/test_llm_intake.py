import pytest
from fastapi.testclient import TestClient

from app.core import config as config_module
from app.main import app
from app.models.user import User
from app.services.auth import hash_password


@pytest.fixture
def client(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core import config as config_module
    from app.db.base import Base
    from app.db.session import get_db

    monkeypatch.setattr(config_module.settings, "storage_dir", str(tmp_path))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def seed_user():
        db = TestingSession()
        try:
            db.add(User(username="llmuser", password_hash=hash_password("Passw0rd!"), full_name="LLM User", is_agent=False))
            db.commit()
        finally:
            db.close()

    seed_user()

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"username": "llmuser", "password": "Passw0rd!"})
    assert r.status_code == 200
    c.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    yield c
    app.dependency_overrides.clear()
    engine.dispose()


def _enable_llm(monkeypatch):
    monkeypatch.setattr(config_module.settings, "llm_provider", "groq")
    monkeypatch.setattr(config_module.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(config_module.settings, "groq_base_url", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(config_module.settings, "groq_model", "openai/gpt-oss-20b")


def test_llm_turn_fills_field_with_source_llm(client, monkeypatch):
    _enable_llm(monkeypatch)
    async def fake_llm(messages, language, pending_fields, current_question=None):
        return {
            "fields": {
                "full_name": {"value": "Ramesh Kumar", "needs_clarification": False, "raw_span": "mera naam Ramesh Kumar"},
            },
            "clarification_question": None,
            "notes": None,
        }

    monkeypatch.setattr("app.services.llm_extraction.extract_fields_llm", fake_llm)

    r = client.post("/api/v1/applications", json={"language": "hinglish", "consent": True})
    assert r.status_code == 201
    app_id = r.json()["application_id"]

    r = client.post(f"/api/v1/applications/{app_id}/messages", json={"message": "mera naam Ramesh Kumar hai"})
    assert r.status_code == 200
    data = r.json()
    assert data["extracted_data"]["full_name"]["value"] == "Ramesh Kumar"
    assert data["extracted_data"]["full_name"]["extraction_source"] == "llm"


def test_llm_bad_value_falls_back_to_rules(client, monkeypatch):
    _enable_llm(monkeypatch)
    async def fake_llm(messages, language, pending_fields, current_question=None):
        return {
            "fields": {
                "phone": {"value": "123", "needs_clarification": False, "raw_span": "phone 123"},
            },
            "clarification_question": None,
            "notes": None,
        }

    monkeypatch.setattr("app.services.llm_extraction.extract_fields_llm", fake_llm)

    # Build an app and advance to phone field via rules for earlier fields.
    r = client.post("/api/v1/applications", json={"language": "hinglish", "consent": True})
    app_id = r.json()["application_id"]
    for msg in ["Ramesh Kumar", "Suresh Kumar", "12/03/1990", "House 12 MG Road Pune", "farmer", "15000", "loan"]:
        client.post(f"/api/v1/applications/{app_id}/messages", json={"message": msg})
    # Now phone field; LLM returns invalid phone "123", rules may validate "9876543210" if typed.
    r = client.post(f"/api/v1/applications/{app_id}/messages", json={"message": "9876543210"})
    assert r.status_code == 200
    data = r.json()
    # Bad LLM value rejected; rules value should be saved.
    assert data["extracted_data"].get("phone", {}).get("value") == "9876543210"
    assert data["extracted_data"]["phone"].get("extraction_source") in ("rules", "llm")


def test_llm_disabled_no_call(client, monkeypatch):
    monkeypatch.setattr(config_module.settings, "llm_provider", "none")
    monkeypatch.setattr(config_module.settings, "groq_api_key", "")
    called = {"n": 0}

    async def fake_llm(*a, **k):
        called["n"] += 1
        return None

    monkeypatch.setattr("app.services.llm_extraction.extract_fields_llm", fake_llm)

    r = client.post("/api/v1/applications", json={"language": "hinglish", "consent": True})
    app_id = r.json()["application_id"]
    client.post(f"/api/v1/applications/{app_id}/messages", json={"message": "Ramesh Kumar"})
    assert called["n"] == 0
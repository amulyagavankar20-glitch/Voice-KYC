import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.user import User
from app.services.auth import hash_password
from app.services.documents import verhoeff_check_digit
from app.services.providers import gnani_client

ANSWERS = [
    "Ramesh Kumar",
    "Suresh Kumar",
    "12/03/1990",
    "House 12, MG Road, Pune, Maharashtra",
    "Farmer",
    "1.5 lakh",
    "Buy seeds and fertilizer",
    "+91 98765 43210",
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def seed_users():
        db = TestingSession()
        try:
            db.add(User(username="applicant1", password_hash=hash_password("Passw0rd!"), full_name="Applicant One", is_agent=False))
            db.add(User(username="agent1", password_hash=hash_password("Passw0rd!"), full_name="Agent One", is_agent=True))
            db.commit()
        finally:
            db.close()

    seed_users()

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


def auth_headers(client, username="applicant1", password="Passw0rd!"):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def agent_headers(client):
    return auth_headers(client, "agent1")


def start(client, consent=True, headers=None):
    return client.post(
        "/api/v1/applications",
        json={"language": "hinglish", "consent": consent},
        headers=headers or auth_headers(client),
    )


def complete_intake(client, language="hinglish", headers=None):
    hdrs = headers or auth_headers(client)
    app_id = client.post(
        "/api/v1/applications", json={"language": language, "consent": True}, headers=hdrs
    ).json()["application_id"]
    for answer in ANSWERS:
        response = client.post(f"/api/v1/applications/{app_id}/messages", json={"message": answer}, headers=hdrs)
        assert response.status_code == 200, response.text
    return app_id, hdrs


def test_consent_is_required(client):
    response = start(client, consent=False)
    assert response.status_code == 400
    assert "consent" in response.json()["detail"].lower()


def test_full_intake_flow_and_approval(client):
    hdrs = auth_headers(client)
    ahdrs = agent_headers(client)
    started = start(client, headers=hdrs)
    assert started.status_code == 201
    body = started.json()
    app_id = body["application_id"]
    assert body["current_field"] == "full_name"
    assert body["progress"] == 0

    for index, answer in enumerate(ANSWERS):
        response = client.post(f"/api/v1/applications/{app_id}/messages", json={"message": answer}, headers=hdrs)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["application_id"] == app_id
        if index < len(ANSWERS) - 1:
            assert body["status"] == "in_progress"
        else:
            assert body["status"] == "pending_review"
            assert body["is_complete"] is True
            assert body["progress"] == 100

    detail = client.get(f"/api/v1/applications/{app_id}", headers=hdrs).json()
    assert detail["status"] == "pending_review"
    assert detail["extracted"]["monthly_income"]["value"] == "150000"
    assert detail["extracted"]["phone"]["value"] == "9876543210"
    assert detail["extracted"]["full_name"]["confidence"] is None
    assert detail["extracted"]["full_name"]["confidence_type"] == "not_assessed"
    assert detail["extracted"]["full_name"]["needs_review"] is True
    roles = [message["role"] for message in detail["conversation"]]
    assert roles[0] == "assistant"
    assert roles[1] == "user"
    assert roles[-1] == "assistant"
    assert len(roles) == 1 + 2 * len(ANSWERS)

    decision = client.post(f"/api/v1/applications/{app_id}/decision", json={"decision": "approve"}, headers=ahdrs)
    assert decision.status_code == 200
    assert decision.json()["status"] == "approve"

    blocked = client.post(f"/api/v1/applications/{app_id}/messages", json={"message": "hello"}, headers=hdrs)
    assert blocked.status_code == 409


def test_approval_blocked_until_all_fields_present(client):
    hdrs = auth_headers(client)
    ahdrs = agent_headers(client)
    app_id = start(client, headers=hdrs).json()["application_id"]
    client.post(f"/api/v1/applications/{app_id}/messages", json={"message": ANSWERS[0]}, headers=hdrs)
    response = client.post(f"/api/v1/applications/{app_id}/decision", json={"decision": "approve"}, headers=ahdrs)
    assert response.status_code == 409


def test_request_info_then_resume(client):
    hdrs = auth_headers(client)
    ahdrs = agent_headers(client)
    app_id = start(client, headers=hdrs).json()["application_id"]
    response = client.post(
        f"/api/v1/applications/{app_id}/decision",
        json={"decision": "request_info", "note": "Please continue."},
        headers=ahdrs,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "request_info"

    resumed = client.post(f"/api/v1/applications/{app_id}/messages", json={"message": ANSWERS[0]}, headers=hdrs)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "in_progress"
    assert resumed.json()["current_field"] == "father_name"


def test_invalid_answer_keeps_application_updated_at(client):
    hdrs = auth_headers(client)
    app_id = start(client, headers=hdrs).json()["application_id"]
    before = client.get(f"/api/v1/applications/{app_id}", headers=hdrs).json()["updated_at"]
    response = client.post(f"/api/v1/applications/{app_id}/messages", json={"message": "12345"}, headers=hdrs)
    assert response.status_code == 200
    assert response.json()["extracted_data"] == {}
    after = client.get(f"/api/v1/applications/{app_id}", headers=hdrs).json()["updated_at"]
    assert after >= before


def test_delete_application(client):
    hdrs = auth_headers(client)
    app_id = start(client, headers=hdrs).json()["application_id"]
    assert client.delete(f"/api/v1/applications/{app_id}", headers=hdrs).status_code == 204
    assert client.get(f"/api/v1/applications/{app_id}", headers=hdrs).status_code == 404


def test_unknown_application_returns_404(client):
    hdrs = auth_headers(client)
    assert client.get("/api/v1/applications/does-not-exist", headers=hdrs).status_code == 404


def test_synthesize_payload_validation(client):
    response = client.post("/api/v1/voice/synthesize", json={"text": "", "language": "hinglish"})
    assert response.status_code == 422
    response = client.post("/api/v1/voice/synthesize", json={"text": "   ", "language": "hinglish"})
    assert response.status_code == 422
    response = client.post("/api/v1/voice/synthesize", json={"text": "hello", "language": "klingon"})
    assert response.status_code == 422


def test_blank_message_rejected(client):
    hdrs = auth_headers(client)
    app_id = start(client, headers=hdrs).json()["application_id"]
    response = client.post(f"/api/v1/applications/{app_id}/messages", json={"message": "   "}, headers=hdrs)
    assert response.status_code == 422


def test_media_type_strips_codec_parameters():
    from app.api.routes.voice import media_type

    assert media_type("audio/webm;codecs=opus") == "audio/webm"
    assert media_type("AUDIO/WAV") == "audio/wav"
    assert media_type(None) == ""


def test_transcribe_rejects_unsupported_type(client):
    response = client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("note.txt", b"not audio", "text/plain")},
        data={"language": "hinglish"},
    )
    assert response.status_code == 415


def test_spoken_sentence_is_reduced_to_the_stated_name(client):
    hdrs = auth_headers(client)
    app_id = start(client, headers=hdrs).json()["application_id"]
    response = client.post(
        f"/api/v1/applications/{app_id}/messages",
        json={"message": "Mera naam Ramesh Kumar hai aur main kheti karta hoon."},
        headers=hdrs,
    )
    assert response.status_code == 200
    body = response.json()
    record = body["extracted_data"]["full_name"]
    assert record["value"] == "Ramesh Kumar"
    assert record["confidence"] is None
    assert record["confidence_type"] == "not_assessed"
    assert record["source"] == "applicant_statement"
    assert record["needs_review"] is True
    assert "Mera naam" in record["evidence"]
    assert body["current_field"] == "father_name"
    assert body["ai_provider"] == "deterministic"


def test_repeated_invalid_answer_adds_an_example(client):
    hdrs = auth_headers(client)
    app_id = start(client, headers=hdrs).json()["application_id"]
    first = client.post(f"/api/v1/applications/{app_id}/messages", json={"message": "12345"}, headers=hdrs).json()
    assert first["current_field"] == "full_name"
    assert first["extracted_data"] == {}
    assert "Jaise:" not in first["reply"]

    second = client.post(f"/api/v1/applications/{app_id}/messages", json={"message": "!!!"}, headers=hdrs).json()
    assert second["current_field"] == "full_name"
    assert "full_name" not in second["extracted_data"]
    assert "Jaise:" in second["reply"]
    assert second["status"] == "in_progress"


def test_client_message_id_makes_sends_idempotent(client):
    hdrs = auth_headers(client)
    app_id = start(client, headers=hdrs).json()["application_id"]
    payload = {"message": "Ramesh Kumar", "client_message_id": "device-abc-1"}
    first = client.post(f"/api/v1/applications/{app_id}/messages", json=payload, headers=hdrs)
    second = client.post(f"/api/v1/applications/{app_id}/messages", json=payload, headers=hdrs)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["current_field"] == "father_name"
    detail = client.get(f"/api/v1/applications/{app_id}", headers=hdrs).json()
    user_messages = [message for message in detail["conversation"] if message["role"] == "user"]
    assert len(user_messages) == 1


def test_document_text_upload_cross_checks_spoken_fields(client):
    app_id, hdrs = complete_intake(client)
    response = client.post(
        f"/api/v1/applications/{app_id}/documents",
        data={
            "doc_type": "pan",
            "text": "Name: Ramesh Kumar\nDOB: 12/03/1990\nPAN: ABCDE1234F",
        },
        headers=hdrs,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["doc_type"] == "pan"
    assert body["extraction_method"] == "applicant_supplied_text"
    assert body["extracted_fields"]["document_number"] == "ABCDE1234F"
    assert body["extracted_fields"]["number_masked"] == "XXXXXX234F"
    assert body["cross_check"]["overall"] == "consistent"
    assert body["cross_check"]["review_flag"] is False


def test_document_mismatch_is_a_review_flag(client):
    app_id, hdrs = complete_intake(client)
    body = client.post(
        f"/api/v1/applications/{app_id}/documents",
        data={"doc_type": "pan", "text": "Name: Suresh Kumar\nDOB: 01/01/1980\nPAN: ZZZZZ9999Z"},
        headers=hdrs,
    ).json()
    assert body["cross_check"]["overall"] == "inconsistent"
    assert body["cross_check"]["review_flag"] is True
    mismatch_fields = {item["field"] for item in body["cross_check"]["mismatches"]}
    assert {"full_name", "date_of_birth"} <= mismatch_fields


def test_aadhaar_checksum_is_enforced(client):
    app_id, hdrs = complete_intake(client)
    base = "23456789012"
    number = base + verhoeff_check_digit(base)
    valid = client.post(
        f"/api/v1/applications/{app_id}/documents",
        data={"doc_type": "aadhaar", "text": f"Name: Ramesh Kumar\nDOB: 12/03/1990\nAadhaar: {number}"},
        headers=hdrs,
    )
    assert valid.status_code == 201, valid.text
    assert valid.json()["extracted_fields"]["number_valid"] is True

    invalid = client.post(
        f"/api/v1/applications/{app_id}/documents",
        data={"doc_type": "aadhaar", "text": "Name: Ramesh Kumar\nAadhaar: 2345 6789 0123"},
        headers=hdrs,
    )
    assert invalid.status_code == 422


def test_assessment_is_rule_based_and_never_approves(client):
    app_id, hdrs = complete_intake(client)
    body = client.get(f"/api/v1/applications/{app_id}/assessment", headers=hdrs).json()
    assert 0 <= body["score"] <= 100
    assert "FIELDS_COMPLETE" in body["reason_codes"]
    assert body["recommendation"] in {"standard_manual_review", "priority_manual_review"}
    assert "not a credit score" in body["disclaimer"]

    detail = client.get(f"/api/v1/applications/{app_id}", headers=hdrs).json()
    assert detail["assessment"]["score"] == body["score"]
    assert detail["status"] == "pending_review"


def test_follow_up_is_localized_and_can_be_sent(client):
    hdrs = auth_headers(client)
    ahdrs = agent_headers(client)
    app_id = client.post(
        "/api/v1/applications", json={"language": "marathi", "consent": True}, headers=hdrs
    ).json()["application_id"]
    for answer in ANSWERS:
        client.post(f"/api/v1/applications/{app_id}/messages", json={"message": answer}, headers=hdrs)
    body = client.post(
        f"/api/v1/applications/{app_id}/follow-up",
        json={"field": "address", "send": True},
        headers=ahdrs,
    ).json()
    assert body["language"] == "marathi"
    assert body["sent"] is True
    assert "पत्ता" in body["question"]

    detail = client.get(f"/api/v1/applications/{app_id}", headers=hdrs).json()
    assert detail["status"] == "request_info"
    assert detail["conversation"][-1]["channel"] == "follow_up"


def test_field_review_does_not_change_applicant_record(client):
    app_id, hdrs = complete_intake(client)
    ahdrs = agent_headers(client)
    response = client.post(
        f"/api/v1/applications/{app_id}/fields/full_name/review",
        json={"status": "verified", "note": "Read back to applicant"},
        headers=ahdrs,
    )
    assert response.status_code == 200
    assert response.json()["applicant_field_unchanged"] is True

    detail = client.get(f"/api/v1/applications/{app_id}", headers=hdrs).json()
    assert detail["reviewed_fields"]["full_name"]["status"] == "verified"
    assert detail["extracted"]["full_name"]["needs_review"] is True
    assert detail["extracted"]["full_name"]["confidence"] is None


def test_field_review_rejects_unknown_field(client):
    app_id, hdrs = complete_intake(client)
    ahdrs = agent_headers(client)
    response = client.post(
        f"/api/v1/applications/{app_id}/fields/credit_score/review",
        json={"status": "verified"},
        headers=ahdrs,
    )
    assert response.status_code == 404


def test_metrics_endpoint_reports_providers_and_counts(client):
    complete_intake(client)
    body = client.get("/api/v1/metrics").json()
    assert "providers" in body
    assert "requests" in body
    assert body["applications"]["pending_review"] == 1
    assert "storage_bytes" in body


def test_capabilities_no_longer_mentions_evon(client):
    body = client.get("/api/v1/capabilities").json()
    assert "evon_configured" not in body
    assert body["text_intake"] is True
    assert body["scorecard"] is True
    assert body["resumable_uploads"] is True


def test_resumable_audio_upload(client):
    from app.services.audio import pcm_to_wav

    hdrs = auth_headers(client)
    app_id = start(client, headers=hdrs).json()["application_id"]
    wav = pcm_to_wav(b"\x00\x00" * 1600, sample_rate=16000)
    split = len(wav) // 2

    created = client.post(
        f"/api/v1/applications/{app_id}/audio/init",
        data={"filename": "answer.wav", "content_type": "audio/wav"},
        headers=hdrs,
    )
    assert created.status_code == 201, created.text
    upload = created.json()
    upload_id = upload["id"]
    assert upload["status"] == "uploading"

    first = client.post(
        f"/api/v1/applications/{app_id}/audio/{upload_id}/chunk",
        data={"offset": 0},
        files={"file": ("part1.wav", wav[:split], "application/octet-stream")},
        headers=hdrs,
    )
    assert first.status_code == 200, first.text
    assert first.json()["received_bytes"] == split

    stale = client.post(
        f"/api/v1/applications/{app_id}/audio/{upload_id}/chunk",
        data={"offset": 0},
        files={"file": ("part1.wav", wav[:split], "application/octet-stream")},
        headers=hdrs,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["expected_offset"] == split

    second = client.post(
        f"/api/v1/applications/{app_id}/audio/{upload_id}/chunk",
        data={"offset": split},
        files={"file": ("part2.wav", wav[split:], "application/octet-stream")},
        headers=hdrs,
    )
    assert second.status_code == 200

    complete = client.post(f"/api/v1/applications/{app_id}/audio/{upload_id}/complete", headers=hdrs)
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "complete"
    assert complete.json()["duration_ms"] == 100

    audio = client.get(f"/api/v1/applications/{app_id}/audio/{upload_id}", headers=hdrs)
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert audio.content[:4] == b"RIFF"


def test_streaming_websocket_returns_partial_and_final(client, monkeypatch):
    from app.services.audio import pcm_to_wav

    monkeypatch.setattr(settings, "gnani_api_key", "test-key")
    seen = {}

    async def fake_transcribe(content, **kwargs):
        seen["bytes"] = content
        seen.update(kwargs)
        return {"text": "Ramesh Kumar", "raw": {}}

    monkeypatch.setattr(gnani_client, "transcribe_bytes", fake_transcribe)

    pcm = b"\x00\x01" * 800
    with client.websocket_connect("/api/v1/voice/stream?language=hinglish") as ws:
        ws.send_json({"type": "start", "sampleRate": 16000})
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(pcm)
        ws.send_json({"type": "stop"})
        final = ws.receive_json()
        done = ws.receive_json()

    assert final["type"] == "final"
    assert final["text"] == "Ramesh Kumar"
    assert done["type"] == "done"
    assert done["text"] == "Ramesh Kumar"
    assert seen["bytes"][:4] == b"RIFF"
    assert seen["language"] == "hinglish"
    assert len(pcm_to_wav(pcm)) == len(seen["bytes"])

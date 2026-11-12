import asyncio
import json
from unittest.mock import MagicMock

from app.core import config as config_module
from app.services import llm_extraction


class _AsyncCM:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        client = MagicMock()

        async def request(*a, **k):
            return self.resp

        client.request = request
        return client

    async def __aexit__(self, *a):
        return False


class _ErrCM:
    async def __aenter__(self):
        raise TimeoutError("boom")

    async def __aexit__(self, *a):
        return False


def _fake_response(status_code=200, body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {}
    resp.text = ""
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=resp
        )
    return resp


def _enable(monkeypatch):
    monkeypatch.setattr(config_module.settings, "llm_provider", "groq")
    monkeypatch.setattr(config_module.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(config_module.settings, "groq_base_url", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(config_module.settings, "groq_model", "openai/gpt-oss-20b")
    monkeypatch.setattr(config_module.settings, "groq_request_timeout_seconds", 5)


def test_llm_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(config_module.settings, "llm_provider", "none")
    monkeypatch.setattr(config_module.settings, "groq_api_key", "")
    result = asyncio.run(llm_extraction.extract_fields_llm([], "hinglish", ["full_name"]))
    assert result is None


def test_llm_success_parses_and_normalizes(monkeypatch):
    _enable(monkeypatch)
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "fields": {
                                "full_name": {
                                    "value": "Ramesh Kumar",
                                    "needs_clarification": False,
                                    "raw_span": "mera naam Ramesh Kumar hai",
                                },
                                "phone": {"value": "9876543210", "needs_clarification": False, "raw_span": None},
                            },
                            "clarification_question": None,
                            "notes": "ok",
                        }
                    )
                }
            }
        ]
    }
    resp = _fake_response(200, payload)
    import app.core.http as http_mod

    monkeypatch.setattr(http_mod, "http_client", lambda timeout: _AsyncCM(resp))
    monkeypatch.setattr(llm_extraction, "http_client", lambda timeout: _AsyncCM(resp))
    result = asyncio.run(
        llm_extraction.extract_fields_llm(
            [{"role": "user", "content": "mera naam Ramesh Kumar hai aur phone 9876543210"}],
            "hinglish",
            ["full_name", "phone"],
        )
    )
    assert result is not None
    assert result["fields"]["full_name"]["value"] == "Ramesh Kumar"
    assert result["fields"]["full_name"]["raw_span"] == "mera naam Ramesh Kumar hai"
    assert result["fields"]["phone"]["value"] == "9876543210"


def test_llm_invalid_json_returns_none(monkeypatch):
    _enable(monkeypatch)
    payload = {"choices": [{"message": {"content": "not json at all"}}]}
    resp = _fake_response(200, payload)
    import app.core.http as http_mod

    monkeypatch.setattr(http_mod, "http_client", lambda timeout: _AsyncCM(resp))
    monkeypatch.setattr(llm_extraction, "http_client", lambda timeout: _AsyncCM(resp))
    result = asyncio.run(llm_extraction.extract_fields_llm([], "hinglish", ["full_name"]))
    assert result is None


def test_llm_http_error_returns_none(monkeypatch):
    _enable(monkeypatch)
    resp = _fake_response(500, {})
    import app.core.http as http_mod

    monkeypatch.setattr(http_mod, "http_client", lambda timeout: _AsyncCM(resp))
    monkeypatch.setattr(llm_extraction, "http_client", lambda timeout: _AsyncCM(resp))
    result = asyncio.run(llm_extraction.extract_fields_llm([], "hinglish", ["full_name"]))
    assert result is None


def test_llm_timeout_returns_none(monkeypatch):
    _enable(monkeypatch)
    import app.core.http as http_mod

    monkeypatch.setattr(http_mod, "http_client", lambda timeout: _ErrCM())
    result = asyncio.run(llm_extraction.extract_fields_llm([], "hinglish", ["full_name"]))
    assert result is None


def test_llm_filters_unknown_fields(monkeypatch):
    _enable(monkeypatch)
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "fields": {
                                "full_name": {"value": "Ramesh", "needs_clarification": False, "raw_span": None},
                                "aadhaar_number": {"value": "1234", "needs_clarification": False, "raw_span": None},
                            },
                            "clarification_question": None,
                            "notes": None,
                        }
                    )
                }
            }
        ]
    }
    resp = _fake_response(200, payload)
    import app.core.http as http_mod

    monkeypatch.setattr(http_mod, "http_client", lambda timeout: _AsyncCM(resp))
    monkeypatch.setattr(llm_extraction, "http_client", lambda timeout: _AsyncCM(resp))
    result = asyncio.run(llm_extraction.extract_fields_llm([], "hinglish", ["full_name"]))
    assert result is not None
    assert "full_name" in result["fields"]
    assert "aadhaar_number" not in result["fields"]
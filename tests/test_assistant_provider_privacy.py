"""The reader never learns which company powers the assistant.

Provider failures reach the reader as one of two generic messages, with a
503; the raw error, model name and endpoint stay in the server log, and the
API key stays out of even that.
"""
import html
import re
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from google.genai import errors

from portfolio_tracker.services import assistant, market_data

FAKE_KEY = "AIza" + "Sy" + "X" * 33

# Written to look like what the SDK actually raises, so a leak of the raw
# text would carry the provider's name, the model and the endpoint with it.
QUOTA_TEXT = ("Resource has been exhausted (e.g. check quota). "
              "Quota exceeded for gemini-3.8-flash on generativelanguage.googleapis.com")
SERVER_TEXT = "Internal error encountered at generativelanguage.googleapis.com (gemini-3.8-flash)"


def quota_error():
    return errors.ClientError(429, {"error": {
        "code": 429, "status": "RESOURCE_EXHAUSTED", "message": QUOTA_TEXT}})


def generic_failures():
    return [
        errors.ServerError(500, {"error": {"code": 500, "status": "INTERNAL",
                                           "message": SERVER_TEXT}}),
        errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE",
                                           "message": SERVER_TEXT}}),
        httpx.ReadTimeout(SERVER_TEXT),
        httpx.ConnectError(SERVER_TEXT),
        ValueError(SERVER_TEXT),  # e.g. a response the SDK could not parse
    ]


class FailingClient:
    def __init__(self, error):
        self.error = error
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        raise self.error


def failing(error):
    return patch.object(assistant, "_get_client", return_value=FailingClient(error))


def quote():
    return patch.object(market_data, "get_quote",
                        return_value=(Decimal("100"), "Apple Inc.", None))


def assert_nothing_leaks(text, raw):
    lowered = text.lower()
    assert "gemini" not in lowered
    assert "google" not in lowered
    assert raw.lower() not in lowered
    assert "traceback" not in lowered


def everything(response):
    """Body and headers: a leak in either counts."""
    headers = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
    return response.get_data(as_text=True) + "\n" + headers


class TestQuotaExhausted:
    def test_api_returns_503_with_the_friendly_message(self, client):
        with failing(quota_error()), quote():
            response = client.post("/api/assistant", json={"question": "q", "symbol": "AAPL"})
        assert response.status_code == 503
        data = response.get_json()
        assert data["ok"] is False and data["kind"] == "unavailable"
        assert data["message"] == assistant.QUOTA_MESSAGE
        assert "quick break due to high demand" in data["message"]
        assert_nothing_leaks(everything(response), QUOTA_TEXT)

    def test_no_javascript_page_shows_the_friendly_message(self, client):
        with failing(quota_error()), quote():
            response = client.post("/assistant", data={"question": "q"})
        body = response.get_data(as_text=True)
        assert "quick break due to high demand" in body
        assert_nothing_leaks(everything(response), QUOTA_TEXT)


class TestOtherFailures:
    @pytest.mark.parametrize("error", generic_failures(),
                             ids=["500", "503", "timeout", "network", "invalid-response"])
    def test_api_returns_503_with_the_generic_message(self, client, error):
        with failing(error), quote():
            response = client.post("/api/assistant", json={"question": "q"})
        assert response.status_code == 503
        data = response.get_json()
        assert data["kind"] == "unavailable"
        assert data["message"] == assistant.UNAVAILABLE_MESSAGE
        assert "temporarily unavailable" in data["message"]
        assert_nothing_leaks(everything(response), SERVER_TEXT)

    @pytest.mark.parametrize("error", generic_failures(),
                             ids=["500", "503", "timeout", "network", "invalid-response"])
    def test_no_javascript_page_shows_the_generic_message(self, client, error):
        with failing(error), quote():
            response = client.post("/assistant", data={"question": "q"})
        assert "temporarily unavailable" in response.get_data(as_text=True)
        assert_nothing_leaks(everything(response), SERVER_TEXT)

    def test_an_unreadable_response_is_unavailable_not_a_500(self, client):
        """ask() never raises, even when reading the response does."""
        reply = SimpleNamespace(prompt_feedback=None, candidates=[SimpleNamespace(
            finish_reason=None,
            content=SimpleNamespace(parts=[SimpleNamespace(text="Hi", thought=False)]))])
        fake = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: reply))
        with patch.object(assistant, "_get_client", return_value=fake), \
                patch.object(assistant, "_grounding", side_effect=TypeError(SERVER_TEXT)), quote():
            response = client.post("/api/assistant", json={"question": "q"})
        assert response.status_code == 503
        assert response.get_json()["message"] == assistant.UNAVAILABLE_MESSAGE
        assert_nothing_leaks(everything(response), SERVER_TEXT)


class TestServerLog:
    def test_logs_type_timestamp_and_detail(self, caplog):
        with failing(quota_error()):
            assistant.ask("ctx", "q")
        assert "ClientError" in caplog.text
        assert "RESOURCE_EXHAUSTED" in caplog.text
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", caplog.text)

    def test_never_logs_the_api_key(self, caplog, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        error = errors.ClientError(400, {"error": {
            "code": 400, "status": "INVALID_ARGUMENT",
            "message": f"API key not valid: {FAKE_KEY}"}})
        with failing(error):
            assistant.ask("ctx", "q")
        assert FAKE_KEY not in caplog.text
        assert "[redacted]" in caplog.text

    def test_redacts_key_shaped_strings_even_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert FAKE_KEY not in assistant._redact(f"url?key={FAKE_KEY}")


class TestSelfDescription:
    def test_system_prompt_keeps_the_provider_unnamed(self):
        prompt = assistant.SYSTEM_PROMPT
        assert "Portfolio Tracker's AI assistant" in prompt
        assert "Do not name or hint at the company, model or service that powers you" in prompt

    def test_system_prompt_itself_names_no_provider(self):
        lowered = assistant.SYSTEM_PROMPT.lower()
        assert "gemini" not in lowered and "google" not in lowered

    def test_notices_name_no_provider(self):
        for text in (assistant.SEARCH_UNAVAILABLE_NOTICE, assistant.QUOTA_MESSAGE,
                     assistant.UNAVAILABLE_MESSAGE):
            assert "gemini" not in text.lower() and "google" not in text.lower()


class TestPages:
    @pytest.mark.parametrize("path", ["/buy", "/assistant"])
    def test_ask_panel_names_no_provider(self, client, path):
        response = client.get(path)
        lowered = everything(response).lower()
        assert "gemini" not in lowered

    def test_script_names_no_provider(self):
        js = open("static/app.js", encoding="utf-8").read().lower()
        assert "gemini" not in js and "google" not in js


# Words that would tell the reader which provider is behind the assistant,
# or how its account is set up. None may reach the reader.
PROVIDER_WORDS = ("gemini", "google", "api key", "billing", "quota", "cloud")

SEARCH_REFUSED_TEXT = (f"Search grounding is not available for key {FAKE_KEY}: enable billing "
                       "on the Google Cloud project (quota: generativelanguage.googleapis.com)")


def search_refused():
    return errors.ClientError(429, {"error": {
        "code": 429, "status": "RESOURCE_EXHAUSTED", "message": SEARCH_REFUSED_TEXT}})


def plain_reply(text="Average cost is what you paid per share."):
    return SimpleNamespace(prompt_feedback=None, candidates=[SimpleNamespace(
        finish_reason=None, grounding_metadata=None,
        content=SimpleNamespace(parts=[SimpleNamespace(text=text, thought=False)]))])


class SearchRefusedClient:
    """Refuses the search request, then answers the same question without it."""

    def __init__(self):
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["config"].tools:
            raise search_refused()
        return plain_reply()


def assert_no_provider_words(text):
    lowered = text.lower()
    for word in PROVIDER_WORDS:
        assert word not in lowered, word


@pytest.fixture
def search_on(monkeypatch):
    """Search enabled and not in its cooldown, whatever an earlier test did."""
    from portfolio_tracker import config
    monkeypatch.setattr(config, "ASSISTANT_SEARCH", True)
    monkeypatch.setattr(assistant, "_search_blocked_until", 0.0)


class TestResearchUnavailable:
    """Search refused (a free key, or an exhausted allowance): the answer
    still arrives, with a notice that says only that research was off."""

    def test_the_notice_names_no_provider_or_account_detail(self):
        assert_no_provider_words(assistant.SEARCH_UNAVAILABLE_NOTICE)
        assert "app's own data" in assistant.SEARCH_UNAVAILABLE_NOTICE

    def test_api_answers_with_the_neutral_notice(self, client, search_on):
        with patch.object(assistant, "_get_client", return_value=SearchRefusedClient()), quote():
            response = client.post("/api/assistant", json={"question": "q"})
        data = response.get_json()
        assert response.status_code == 200 and data["ok"]
        assert data["notice"] == assistant.SEARCH_UNAVAILABLE_NOTICE
        assert data["research"] is False
        body = everything(response)
        assert SEARCH_REFUSED_TEXT.lower() not in body.lower()
        assert_no_provider_words(data["notice"] + data["message"])

    def test_the_next_question_in_the_cooldown_says_the_same(self, client, search_on):
        fake = SearchRefusedClient()
        with patch.object(assistant, "_get_client", return_value=fake), quote():
            client.post("/api/assistant", json={"question": "first"})
            data = client.post("/api/assistant", json={"question": "second"}).get_json()
        assert not fake.calls[-1]["config"].tools        # search not even tried
        assert data["notice"] == assistant.SEARCH_UNAVAILABLE_NOTICE

    def test_no_javascript_page_shows_the_neutral_notice(self, client, search_on):
        with patch.object(assistant, "_get_client", return_value=SearchRefusedClient()), quote():
            response = client.post("/assistant", data={"question": "q"})
        body = " ".join(html.unescape(response.get_data(as_text=True)).split())
        assert assistant.SEARCH_UNAVAILABLE_NOTICE in body
        assert "billing" not in body.lower() and "gemini" not in body.lower()
        assert SEARCH_REFUSED_TEXT.lower() not in body.lower()

    def test_the_real_reason_is_logged_with_the_key_redacted(self, caplog, monkeypatch, search_on):
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        with patch.object(assistant, "_get_client", return_value=SearchRefusedClient()):
            answer = assistant.ask("ctx", "q")
        assert answer.ok
        assert "web search refused" in caplog.text
        assert "ClientError" in caplog.text and "Search grounding is not available" in caplog.text
        assert FAKE_KEY not in caplog.text and "[redacted]" in caplog.text

    def test_a_restored_answer_shows_todays_wording(self, client):
        """The drawer replays saved answers; a saved notice must not bring
        back text the app no longer uses."""
        page = client.get("/buy").get_data(as_text=True)
        assert f'data-research-notice="{assistant.SEARCH_UNAVAILABLE_NOTICE}"'.replace("'", "&#39;") in page
        js = open("static/app.js", encoding="utf-8").read()
        assert "turn.notice ? (drawer.dataset.researchNotice || turn.notice)" in js

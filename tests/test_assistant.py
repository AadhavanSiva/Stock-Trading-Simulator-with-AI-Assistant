"""Assistant tests. The Claude API is always mocked — see conftest's
no_real_api_calls guard, which fails any test that forgets to."""
import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import httpx2
import pytest

import web as web_module
from portfolio_tracker import config, operations
from portfolio_tracker.models import portfolio
from portfolio_tracker.services import assistant, market_data

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, code):
    return cls(message="upstream said no", response=httpx2.Response(code, request=REQ), body=None)


def reply(text="Average cost is what you paid per share, on average.",
          stop_reason="end_turn", blocks=None):
    content = blocks if blocks is not None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(cache_read_input_tokens=0,
                              cache_creation_input_tokens=0, input_tokens=12),
        _request_id="req_test",
    )


class FakeClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response if response is not None else reply()
        self.error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def using(client):
    return patch.object(assistant, "_get_client", return_value=client)


@pytest.fixture(autouse=True)
def reset_rate_limit():
    web_module._asked.clear()
    yield
    web_module._asked.clear()


def text(response):
    return response.get_data(as_text=True)


class TestRequestShape:
    def ask(self, context="Price: $10", question="What is a share?", **kw):
        client = FakeClient()
        with using(client):
            assistant.ask(context, question, **kw)
        return client.calls[0]

    def test_uses_claude_opus_5(self):
        assert self.ask()["model"] == "claude-opus-5"

    def test_refusal_fallbacks_are_on_by_default(self):
        call = self.ask()
        assert call["fallbacks"] == "default"
        assert call["betas"] == ["server-side-fallback-2026-07-01"]

    def test_a_model_without_fallback_support_goes_without(self, monkeypatch):
        """Sending fallbacks to a model that does not accept them is a 400."""
        monkeypatch.setattr(config, "ASSISTANT_MODEL", "claude-sonnet-5")
        call = self.ask()
        assert "fallbacks" not in call and "betas" not in call

    def test_thinking_and_effort(self):
        call = self.ask()
        assert call["thinking"] == {"type": "adaptive"}
        assert call["output_config"] == {"effort": "medium"}

    def test_system_prompt_is_marked_for_caching(self):
        system = self.ask()["system"]
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert system[0]["text"] == assistant.SYSTEM_PROMPT

    def test_app_data_and_question_go_in_the_user_turn_not_the_system_prompt(self):
        """Anything that varies must sit after the cached prefix."""
        call = self.ask(context="Current price: $332.27", question="Is this high?")
        assert "$332.27" not in call["system"][0]["text"]
        user = call["messages"][0]
        assert user["role"] == "user"
        assert "<app_data>" in user["content"] and "$332.27" in user["content"]
        assert "Is this high?" in user["content"]

    def test_system_prompt_is_byte_identical_across_requests(self):
        first = self.ask(context="Price: $1")["system"][0]["text"]
        second = self.ask(context="Price: $999")["system"][0]["text"]
        assert first == second

    def test_system_prompt_holds_nothing_volatile(self):
        """A date or figure in the prefix would break caching every request."""
        prompt = assistant.SYSTEM_PROMPT
        assert date.today().isoformat() not in prompt
        assert str(date.today().year) not in prompt
        assert not re.search(r"\$\d+\.\d\d", prompt)

    def test_system_prompt_is_long_enough_to_cache(self):
        """Claude Opus 5 caches prefixes from 512 tokens; ~4 chars per token."""
        assert len(assistant.SYSTEM_PROMPT) / 4 > 512

    def test_only_one_user_message_is_sent(self):
        call = self.ask(earlier=[{"question": "q1", "answer": "a1"}])
        assert [m["role"] for m in call["messages"]] == ["user"]


class TestSystemPromptPolicy:
    """The line between teaching and advising lives in the prompt."""

    def test_forbids_buy_sell_recommendations(self):
        assert "Do not tell someone to buy, sell or hold" in assistant.SYSTEM_PROMPT

    def test_forbids_price_predictions(self):
        assert "do not predict where a price is heading" in assistant.SYSTEM_PROMPT

    def test_requires_figures_to_come_from_app_data(self):
        assert "Never produce a figure that is not in the data" in assistant.SYSTEM_PROMPT

    def test_asks_for_plain_text_not_markdown(self):
        assert "do not use markdown" in assistant.SYSTEM_PROMPT


class TestAnswers:
    def test_returns_the_text(self):
        with using(FakeClient(reply("Plain answer."))):
            answer = assistant.ask("ctx", "q")
        assert answer.ok and answer.text == "Plain answer."

    def test_ignores_non_text_blocks(self):
        """Fallback and thinking blocks must never leak into the panel."""
        blocks = [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="fallback"),
            SimpleNamespace(type="text", text="Only this."),
        ]
        with using(FakeClient(reply(blocks=blocks))):
            assert assistant.ask("ctx", "q").text == "Only this."

    def test_a_refusal_is_reported_without_reading_content(self):
        client = FakeClient(reply("partial text", stop_reason="refusal"))
        with using(client):
            answer = assistant.ask("ctx", "q")
        assert not answer.ok
        assert answer.kind == "refused"
        assert "partial text" not in answer.message

    def test_empty_output_is_a_failure_not_a_blank_answer(self):
        with using(FakeClient(reply(blocks=[]))):
            answer = assistant.ask("ctx", "q")
        assert not answer.ok and answer.message


class TestFailures:
    @pytest.mark.parametrize("error,kind", [
        (status_error(anthropic.AuthenticationError, 401), "not_configured"),
        (status_error(anthropic.PermissionDeniedError, 403), "not_configured"),
        (status_error(anthropic.RateLimitError, 429), "busy"),
        (status_error(anthropic.BadRequestError, 400), "failed"),
        (status_error(anthropic.InternalServerError, 500), "failed"),
        (anthropic.APITimeoutError(request=REQ), "failed"),
        (anthropic.APIConnectionError(request=REQ), "failed"),
    ])
    def test_each_api_error_becomes_a_readable_answer(self, error, kind):
        with using(FakeClient(error=error)):
            answer = assistant.ask("ctx", "q")
        assert not answer.ok
        assert answer.kind == kind
        assert answer.message
        assert "Error" not in answer.message and "Traceback" not in answer.message

    def test_timeout_is_not_mistaken_for_a_network_outage(self):
        """APITimeoutError subclasses APIConnectionError; catching the parent
        first would tell people to check their internet connection."""
        with using(FakeClient(error=anthropic.APITimeoutError(request=REQ))):
            message = assistant.ask("ctx", "q").message
        assert "took too long" in message
        assert "internet" not in message

    def test_missing_credentials_explains_the_setup(self):
        """With no key the SDK raises TypeError, not an API error class."""
        error = TypeError("Could not resolve authentication method. Expected one of api_key...")
        with using(FakeClient(error=error)):
            answer = assistant.ask("ctx", "q")
        assert answer.kind == "not_configured"
        assert "ANTHROPIC_API_KEY" in answer.message

    def test_an_unrelated_type_error_is_not_swallowed(self):
        with using(FakeClient(error=TypeError("genuine bug"))):
            with pytest.raises(TypeError):
                assistant.ask("ctx", "q")

    def test_server_errors_say_to_try_later(self):
        with using(FakeClient(error=status_error(anthropic.InternalServerError, 503))):
            assert "few minutes" in assistant.ask("ctx", "q").message


class TestValidation:
    def test_empty_question_never_calls_the_api(self):
        client = FakeClient()
        with using(client):
            assert assistant.ask("ctx", "   ").kind == "invalid"
        assert client.calls == []

    def test_overlong_question_never_calls_the_api(self):
        client = FakeClient()
        with using(client):
            answer = assistant.ask("ctx", "x" * (assistant.MAX_QUESTION_CHARS + 1))
        assert answer.kind == "invalid"
        assert client.calls == []

    def test_disabled_never_calls_the_api(self, monkeypatch):
        monkeypatch.setattr(config, "ASSISTANT_ENABLED", False)
        client = FakeClient()
        with using(client):
            assert assistant.ask("ctx", "q").kind == "disabled"
        assert client.calls == []


class TestEarlierTurns:
    def test_are_quoted_inside_the_single_user_message(self):
        message = assistant.build_user_message(
            "ctx", "and now?", [{"question": "What is a share?", "answer": "A unit."}]
        )
        assert "<earlier_in_this_conversation>" in message
        assert "What is a share?" in message and "A unit." in message
        assert message.index("<earlier") < message.index("<question>")

    def test_are_capped(self):
        earlier = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(9)]
        message = assistant.build_user_message("ctx", "now", earlier)
        assert "q8" in message and "q6" in message
        assert "q5" not in message

    def test_incomplete_turns_are_dropped(self):
        message = assistant.build_user_message("ctx", "now", [{"question": "q", "answer": ""}])
        assert "<earlier" not in message


def quote(price="332.27", name="Apple Inc."):
    return patch.object(market_data, "get_quote", return_value=(Decimal(price), name))


class TestContext:
    def test_includes_price_and_position(self, user):
        portfolio.record_purchase(user, "AAPL", "Apple Inc.", Decimal("300"), Decimal("2"))
        with quote():
            context = operations.assistant_context(user, "aapl")
        assert "Apple Inc." in context
        assert "$332.27" in context
        assert "2 shares at an average cost of $300.00" in context

    def test_when_not_owned_it_says_so(self, user):
        with quote():
            context = operations.assistant_context(user, "AAPL")
        assert "They do not own any" in context

    def test_excludes_other_accounts(self, user, other_user):
        portfolio.record_purchase(other_user, "SECRET", "Their Co", Decimal("10"), Decimal("5"))
        with quote():
            context = operations.assistant_context(user, "AAPL")
        assert "SECRET" not in context and "Their Co" not in context

    def test_unknown_ticker_is_stated_not_invented(self, user):
        with patch.object(market_data, "get_quote", return_value=(None, "X")):
            context = operations.assistant_context(user, "NOPE")
        assert "No market data could be found for NOPE" in context

    def test_without_a_symbol_it_describes_the_portfolio(self, user):
        portfolio.record_purchase(user, "AAPL", "Apple Inc.", Decimal("300"), Decimal("2"))
        context = operations.assistant_context(user)
        assert "Their holdings:" in context and "AAPL" in context
        assert "looking at" not in context

    def test_says_money_is_pretend(self, user):
        assert "pretend" in operations.assistant_context(user)


class TestRoutes:
    def post(self, client, **payload):
        return client.post("/api/assistant", json=payload)

    def test_api_requires_signing_in(self, anon):
        assert anon.post("/api/assistant", json={"question": "hi"}).status_code == 302

    def test_page_requires_signing_in(self, anon):
        assert anon.get("/assistant").status_code == 302

    def test_non_json_is_refused_without_calling_the_api(self, client):
        """Blocks a plain cross-site form from spending money on your behalf."""
        fake = FakeClient()
        with using(fake):
            response = client.post("/api/assistant", data={"question": "hi"})
        assert response.status_code == 415
        assert fake.calls == []

    def test_answers_a_question(self, client):
        with using(FakeClient(reply("Here you go."))), quote():
            response = self.post(client, question="What is AAPL?", symbol="AAPL")
        assert response.status_code == 200
        assert response.get_json() == {
            "ok": True, "answer": "Here you go.", "kind": "answered", "message": "",
        }

    def test_figures_sent_by_the_browser_never_reach_the_model(self, client):
        """Only the ticker comes from the page; numbers come from the server."""
        fake = FakeClient()
        with using(fake), quote("332.27"):
            self.post(client, question="q", symbol="AAPL", price="$0.01", cash="$9999999")
        sent = fake.calls[0]["messages"][0]["content"]
        assert "$0.01" not in sent and "9999999" not in sent
        assert "$332.27" in sent

    def test_malformed_earlier_turns_are_ignored(self, client):
        fake = FakeClient()
        with using(fake):
            response = self.post(client, question="q", earlier="not a list")
        assert response.status_code == 200

    def test_setup_problem_returns_503_with_guidance(self, client):
        error = TypeError("Could not resolve authentication method.")
        with using(FakeClient(error=error)):
            response = self.post(client, question="q")
        assert response.status_code == 503
        assert "ANTHROPIC_API_KEY" in response.get_json()["message"]

    def test_rate_limited_per_account(self, client, monkeypatch):
        monkeypatch.setattr(web_module, "ASSISTANT_LIMIT", 2)
        fake = FakeClient()
        with using(fake):
            codes = [self.post(client, question="q").status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        assert len(fake.calls) == 2, "the third question must not reach the API"

    def test_no_javascript_page_answers_a_question(self, client):
        with using(FakeClient(reply("First idea.\n\nSecond idea."))), quote():
            body = text(client.post("/assistant", data={"question": "Explain", "symbol": "AAPL"}))
        assert "<p>First idea.</p>" in body and "<p>Second idea.</p>" in body

    def test_model_output_is_escaped_on_the_page(self, client):
        """An answer is untrusted text from an external service."""
        with using(FakeClient(reply('<script>alert("x")</script>'))):
            body = text(client.post("/assistant", data={"question": "q"}))
        assert '<script>alert("x")</script>' not in body
        assert "&lt;script&gt;" in body

    def test_no_javascript_page_shows_errors_readably(self, client):
        with using(FakeClient(error=status_error(anthropic.RateLimitError, 429))):
            body = text(client.post("/assistant", data={"question": "q"}))
        assert "too many questions" in body


class TestDrawer:
    def test_present_when_signed_in(self, client):
        body = text(client.get("/buy"))
        assert 'data-assistant ' in body or "data-assistant\n" in body
        assert "data-assistant-toggle" in body

    def test_absent_when_signed_out(self, anon):
        assert "data-assistant-toggle" not in text(anon.get("/"))

    def test_absent_when_disabled(self, client, monkeypatch):
        monkeypatch.setattr(config, "ASSISTANT_ENABLED", False)
        assert "data-assistant-toggle" not in text(client.get("/buy"))

    def test_toggle_is_a_real_link_for_no_javascript(self, client):
        body = text(client.get("/buy"))
        toggle = re.search(r'<a class="assistant-toggle"[^>]*>', body).group(0)
        assert 'href="/assistant"' in toggle

    def test_drawer_starts_hidden(self, client):
        body = text(client.get("/buy"))
        aside = re.search(r"<aside[^>]*data-assistant[^>]*>", body).group(0)
        assert " hidden" in aside

    def test_stock_pages_tell_the_drawer_which_ticker(self, client):
        with quote():
            body = text(client.get("/stock/AAPL"))
        assert 'data-symbol="AAPL"' in body
        assert 'href="/assistant?symbol=AAPL"' in body

    def test_the_log_is_a_live_region(self, client):
        body = text(client.get("/buy"))
        assert 'role="log"' in body and 'aria-live="polite"' in body

    def test_answers_are_placed_with_text_content_never_inner_html(self):
        app_js = open("static/app.js", encoding="utf-8").read()
        block = app_js[app_js.index("assistant\n"):app_js.index("function currency")]
        assert "textContent = chunk" in block
        assert "innerHTML = data" not in block
        assert not re.search(r"innerHTML\s*=\s*[^'\"]*answer", block)

    def test_drawer_motion_is_disabled_under_reduced_motion(self):
        css = open("static/style.css", encoding="utf-8").read()
        assert ".assistant, .assistant-toggle { transition: none !important; }" in css

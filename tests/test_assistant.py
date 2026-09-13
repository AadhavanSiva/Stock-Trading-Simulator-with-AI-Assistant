"""Assistant tests. The Gemini API is always mocked — see conftest's
no_real_api_calls guard, which fails any test that forgets to."""
import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from google.genai import errors, types

import web as web_module
from portfolio_tracker import config, operations
from portfolio_tracker.models import portfolio
from portfolio_tracker.services import assistant, market_data


def client_error(code, message="nope", status="INVALID_ARGUMENT", reason=None):
    body = {"error": {"code": code, "message": message, "status": status}}
    if reason:
        body["error"]["details"] = [{"reason": reason}]
    return errors.ClientError(code, body)


def server_error(code=503):
    return errors.ServerError(code, {"error": {"code": code, "message": "overloaded",
                                               "status": "UNAVAILABLE"}})


def part(text, thought=False):
    return SimpleNamespace(text=text, thought=thought)


def reply(text="Average cost is what you paid per share, on average.",
          finish="STOP", parts=None, block_reason=None, candidates=True):
    feedback = SimpleNamespace(block_reason=block_reason) if block_reason else None
    if not candidates:
        return SimpleNamespace(prompt_feedback=feedback, candidates=[])
    parts = parts if parts is not None else [part(text)]
    candidate = SimpleNamespace(
        finish_reason=getattr(types.FinishReason, finish),
        content=SimpleNamespace(parts=parts),
    )
    return SimpleNamespace(prompt_feedback=feedback, candidates=[candidate])


class FakeClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response if response is not None else reply()
        self.error = error
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
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

    def test_uses_the_configured_gemini_model(self):
        assert self.ask()["model"] == "gemini-3.8-flash"

    def test_system_instruction_is_the_frozen_prompt(self):
        assert self.ask()["config"].system_instruction == assistant.SYSTEM_PROMPT

    def test_thinking_level_comes_from_config(self):
        cfg = self.ask()["config"]
        assert cfg.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM

    def test_an_unsupported_thinking_level_falls_back_instead_of_a_400(self, monkeypatch):
        monkeypatch.setattr(config, "ASSISTANT_THINKING", "extreme")
        cfg = self.ask()["config"]
        assert cfg.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM

    def test_automatic_function_calling_is_off(self):
        """No tools are sent; left on, the SDK logs a warning every request."""
        assert self.ask()["config"].automatic_function_calling.disable is True

    def test_leaves_room_for_thinking_in_the_output_limit(self):
        assert self.ask()["config"].max_output_tokens >= 4096

    def test_app_data_and_question_go_in_contents_not_the_instructions(self):
        call = self.ask(context="Current price: $332.27", question="Is this high?")
        assert "$332.27" not in call["config"].system_instruction
        assert "<app_data>" in call["contents"] and "$332.27" in call["contents"]
        assert "Is this high?" in call["contents"]

    def test_instructions_are_identical_across_requests(self):
        first = self.ask(context="Price: $1")["config"].system_instruction
        second = self.ask(context="Price: $999")["config"].system_instruction
        assert first == second

    def test_instructions_hold_nothing_volatile(self):
        prompt = assistant.SYSTEM_PROMPT
        assert date.today().isoformat() not in prompt
        assert not re.search(r"\$\d+\.\d\d", prompt)


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

    def test_joins_multiple_text_parts(self):
        with using(FakeClient(reply(parts=[part("One. "), part("Two.")]))):
            assert assistant.ask("ctx", "q").text == "One. Two."

    def test_never_shows_a_thought_as_the_answer(self):
        parts = [part("private reasoning", thought=True), part("The answer.")]
        with using(FakeClient(reply(parts=parts))):
            answer = assistant.ask("ctx", "q")
        assert answer.text == "The answer."
        assert "private reasoning" not in answer.text

    @pytest.mark.parametrize("finish", ["SAFETY", "RECITATION", "BLOCKLIST",
                                        "PROHIBITED_CONTENT", "SPII"])
    def test_a_withheld_answer_discards_any_partial_text(self, finish):
        with using(FakeClient(reply("partial text", finish=finish))):
            answer = assistant.ask("ctx", "q")
        assert answer.kind == "refused"
        assert "partial text" not in answer.message

    def test_a_blocked_question_is_reported_as_refused(self):
        with using(FakeClient(reply(block_reason="SAFETY", candidates=False))):
            assert assistant.ask("ctx", "q").kind == "refused"

    def test_no_candidates_is_a_failure_not_a_blank_answer(self):
        with using(FakeClient(reply(candidates=False))):
            answer = assistant.ask("ctx", "q")
        assert not answer.ok and answer.message

    def test_running_out_of_tokens_before_answering_says_so(self):
        with using(FakeClient(reply(parts=[], finish="MAX_TOKENS"))):
            assert "ran out of room" in assistant.ask("ctx", "q").message


class TestFailures:
    def test_missing_key_explains_the_setup(self):
        """google-genai raises ValueError when the client is created without a key."""
        with patch.object(assistant, "_get_client", side_effect=ValueError("No API key was provided.")):
            answer = assistant.ask("ctx", "q")
        assert answer.kind == "not_configured"
        assert "GEMINI_API_KEY" in answer.message
        assert "aistudio.google.com" in answer.message

    def test_an_invalid_key_is_not_mistaken_for_a_bad_question(self):
        """Gemini reports a bad key as 400 INVALID_ARGUMENT, not 401."""
        error = client_error(400, "API key not valid. Please pass a valid API key.",
                             reason="API_KEY_INVALID")
        with using(FakeClient(error=error)):
            answer = assistant.ask("ctx", "q")
        assert answer.kind == "not_configured"
        assert "rephras" not in answer.message
        assert "GEMINI_API_KEY" in answer.message

    def test_an_ordinary_bad_request_suggests_rephrasing(self):
        with using(FakeClient(error=client_error(400, "Invalid contents"))):
            answer = assistant.ask("ctx", "q")
        assert answer.kind == "failed" and "rephras" in answer.message

    @pytest.mark.parametrize("code", [401, 403])
    def test_permission_problems_point_at_the_key(self, code):
        with using(FakeClient(error=client_error(code, status="PERMISSION_DENIED"))):
            assert assistant.ask("ctx", "q").kind == "not_configured"

    def test_an_unknown_model_names_the_setting_to_change(self):
        with using(FakeClient(error=client_error(404, status="NOT_FOUND"))):
            answer = assistant.ask("ctx", "q")
        assert "ASSISTANT_MODEL" in answer.message and "gemini-3.8-flash" in answer.message

    def test_quota_exhaustion_is_busy(self):
        with using(FakeClient(error=client_error(429, status="RESOURCE_EXHAUSTED"))):
            answer = assistant.ask("ctx", "q")
        assert answer.kind == "busy" and "limit" in answer.message

    def test_server_errors_say_to_try_later(self):
        with using(FakeClient(error=server_error(503))):
            answer = assistant.ask("ctx", "q")
        assert answer.kind == "failed" and "few minutes" in answer.message

    def test_timeout_is_not_mistaken_for_a_network_outage(self):
        """httpx.TimeoutException is itself a RequestError; order matters."""
        with using(FakeClient(error=httpx.ConnectTimeout("timed out"))):
            message = assistant.ask("ctx", "q").message
        assert "took too long" in message and "internet" not in message

    def test_network_failure_is_reported(self):
        """These arrive as raw httpx errors, not google.genai APIError."""
        with using(FakeClient(error=httpx.ConnectError("boom"))):
            assert "internet connection" in assistant.ask("ctx", "q").message

    def test_no_failure_message_leaks_internals(self):
        for error in (client_error(400), client_error(429), server_error(500),
                      httpx.ConnectError("boom"), httpx.ReadTimeout("slow")):
            with using(FakeClient(error=error)):
                message = assistant.ask("ctx", "q").message
            assert message and "Traceback" not in message and "Error" not in message


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
        assert answer.kind == "invalid" and client.calls == []

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
        assert message.index("<earlier") < message.index("<question>")

    def test_are_capped(self):
        earlier = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(9)]
        message = assistant.build_user_message("ctx", "now", earlier)
        assert "q8" in message and "q6" in message and "q5" not in message

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
        assert "Apple Inc." in context and "$332.27" in context
        assert "2 shares at an average cost of $300.00" in context

    def test_excludes_other_accounts(self, user, other_user):
        portfolio.record_purchase(other_user, "SECRET", "Their Co", Decimal("10"), Decimal("5"))
        with quote():
            context = operations.assistant_context(user, "AAPL")
        assert "SECRET" not in context and "Their Co" not in context

    def test_never_includes_the_persons_name_or_email(self, user):
        """Sent to Google — keep identifying details out of it."""
        context = operations.assistant_context(user)
        assert "tester@example.com" not in context and "Tester" not in context

    def test_unknown_ticker_is_stated_not_invented(self, user):
        with patch.object(market_data, "get_quote", return_value=(None, "X")):
            context = operations.assistant_context(user, "NOPE")
        assert "No market data could be found for NOPE" in context

    def test_without_a_symbol_it_describes_the_portfolio(self, user):
        portfolio.record_purchase(user, "AAPL", "Apple Inc.", Decimal("300"), Decimal("2"))
        context = operations.assistant_context(user)
        assert "Their holdings:" in context and "looking at" not in context


class TestRoutes:
    def post(self, client, **payload):
        return client.post("/api/assistant", json=payload)

    def test_api_requires_signing_in(self, anon):
        assert anon.post("/api/assistant", json={"question": "hi"}).status_code == 302

    def test_non_json_is_refused_without_calling_the_api(self, client):
        fake = FakeClient()
        with using(fake):
            response = client.post("/api/assistant", data={"question": "hi"})
        assert response.status_code == 415 and fake.calls == []

    def test_answers_a_question(self, client):
        with using(FakeClient(reply("Here you go."))), quote():
            response = self.post(client, question="What is AAPL?", symbol="AAPL")
        assert response.status_code == 200
        assert response.get_json()["answer"] == "Here you go."

    def test_figures_sent_by_the_browser_never_reach_the_model(self, client):
        fake = FakeClient()
        with using(fake), quote("332.27"):
            self.post(client, question="q", symbol="AAPL", price="$0.01", cash="$9999999")
        sent = fake.calls[0]["contents"]
        assert "$0.01" not in sent and "9999999" not in sent and "$332.27" in sent

    def test_setup_problem_returns_503_with_guidance(self, client):
        with patch.object(assistant, "_get_client", side_effect=ValueError("No API key")):
            response = self.post(client, question="q")
        assert response.status_code == 503
        assert "GEMINI_API_KEY" in response.get_json()["message"]

    def test_rate_limited_per_account(self, client, monkeypatch):
        monkeypatch.setattr(web_module, "ASSISTANT_LIMIT", 2)
        fake = FakeClient()
        with using(fake):
            codes = [self.post(client, question="q").status_code for _ in range(3)]
        assert codes == [200, 200, 429] and len(fake.calls) == 2

    def test_no_javascript_page_answers_a_question(self, client):
        with using(FakeClient(reply("First idea.\n\nSecond idea."))), quote():
            body = text(client.post("/assistant", data={"question": "Explain", "symbol": "AAPL"}))
        assert "<p>First idea.</p>" in body and "<p>Second idea.</p>" in body

    def test_model_output_is_escaped_on_the_page(self, client):
        with using(FakeClient(reply('<script>alert("x")</script>'))):
            body = text(client.post("/assistant", data={"question": "q"}))
        assert '<script>alert("x")</script>' not in body and "&lt;script&gt;" in body


class TestDisclosure:
    """Questions go to Google. On a free key Google may use them to improve
    its products and people may read them, so the panel says so."""

    def test_drawer_says_questions_go_to_google(self, client):
        body = text(client.get("/buy"))
        assert "Google" in body and "personal" in body

    def test_full_page_says_questions_go_to_google(self, client):
        body = text(client.get("/assistant"))
        assert "Google" in body and "personal" in body


class TestDrawer:
    def test_present_when_signed_in(self, client):
        assert "data-assistant-toggle" in text(client.get("/buy"))

    def test_absent_when_signed_out(self, anon):
        assert "data-assistant-toggle" not in text(anon.get("/"))

    def test_toggle_is_a_real_link_for_no_javascript(self, client):
        toggle = re.search(r'<a class="assistant-toggle"[^>]*>', text(client.get("/buy"))).group(0)
        assert 'href="/assistant"' in toggle

    def test_answers_are_placed_with_text_content_never_inner_html(self):
        app_js = open("static/app.js", encoding="utf-8").read()
        block = app_js[app_js.index("assistant\n"):app_js.index("function currency")]
        assert "textContent = chunk" in block
        assert not re.search(r"innerHTML\s*=\s*[^'\"]*answer", block)

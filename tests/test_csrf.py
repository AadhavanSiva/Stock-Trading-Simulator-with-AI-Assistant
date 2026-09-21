"""CSRF protection: every state-changing request needs a valid token.

The general-purpose `client` and `anon` fixtures switch CSRF checks off so
other tests can post forms directly. Everything here switches them back on.
"""
import glob
import os
import re
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.genai import types

import web as web_module
from portfolio_tracker import config
from portfolio_tracker.db import cursor
from portfolio_tracker.models import portfolio, stocks, users
from portfolio_tracker.services import assistant, market_data

ROOT = os.path.dirname(os.path.dirname(__file__))
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def text(response):
    return response.get_data(as_text=True)


def signed_in_client(user_id, monkeypatch):
    web_module.app.config.update(TESTING=True)
    monkeypatch.setitem(web_module.app.config, "WTF_CSRF_ENABLED", True)
    c = web_module.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user_id
    return c


@pytest.fixture
def csrf_client(user, monkeypatch):
    with signed_in_client(user, monkeypatch) as c:
        yield c


def token_from(c, path="/buy"):
    match = TOKEN_RE.search(text(c.get(path)))
    assert match, f"no CSRF token rendered on {path}"
    return match.group(1)


def quote(price="100", name="Test Company Inc."):
    return patch.object(market_data, "get_quote",
                        return_value=(Decimal(price), name, None))


def own(user, shares="10", price="100"):
    stocks.upsert_stock("TST", "Test Company Inc.", Decimal(price))
    portfolio.record_purchase(user, "TST", "Test Company Inc.", Decimal(price), Decimal(shares))


def assistant_rows():
    with cursor() as cur:
        cur.execute("SELECT count(*) FROM assistant_requests")
        return cur.fetchone()[0]


# Every route that changes state or spends money, with a body that would
# otherwise be accepted.
STATE_CHANGING = [
    ("/buy/lookup", {"symbol": "TST"}),
    ("/buy/review", {"symbol": "TST", "shares": "1"}),
    ("/buy/confirm", {"symbol": "TST", "shares": "1"}),
    ("/sell/TST/review", {"shares": "1"}),
    ("/sell/TST/confirm", {"shares": "1"}),
    ("/actions/refresh-prices", {}),
    ("/actions/load-history", {}),
    ("/stock/TST/fetch", {"range": "1y"}),
    ("/assistant", {"question": "What is this?", "symbol": "TST"}),
    ("/logout", {}),
]


class TestRequestsWithoutAValidTokenAreRejected:
    @pytest.mark.parametrize("path,form", STATE_CHANGING)
    def test_missing_token(self, csrf_client, path, form):
        response = csrf_client.post(path, data=form)
        assert response.status_code == 400
        assert "This page has expired" in text(response)

    @pytest.mark.parametrize("path,form", STATE_CHANGING)
    def test_forged_token(self, csrf_client, path, form):
        response = csrf_client.post(path, data=dict(form, csrf_token="forged-token-value"))
        assert response.status_code == 400

    def test_a_token_from_another_session_is_refused(self, user, other_user, monkeypatch):
        with signed_in_client(other_user, monkeypatch) as attacker:
            stolen = token_from(attacker)
        with signed_in_client(user, monkeypatch) as victim, quote():
            response = victim.post("/buy/confirm", data={
                "symbol": "TST", "shares": "1", "csrf_token": stolen})
        assert response.status_code == 400
        assert portfolio.get_holding(user, "TST") is None

    def test_a_rejected_buy_changes_nothing(self, csrf_client, user):
        with quote():
            csrf_client.post("/buy/confirm", data={"symbol": "TST", "shares": "5"})
        assert portfolio.get_holding(user, "TST") is None
        assert users.get_cash(user) == users.starting_cash()

    def test_a_rejected_sale_changes_nothing(self, csrf_client, user):
        own(user)
        with patch.object(market_data, "get_live_price", return_value=Decimal("150")):
            csrf_client.post("/sell/TST/confirm", data={"shares": "10"})
        assert portfolio.get_holding(user, "TST")[0] == Decimal("10")

    def test_a_rejected_refresh_never_calls_the_provider(self, csrf_client, user):
        own(user)
        with patch.object(market_data, "get_live_price") as live:
            csrf_client.post("/actions/refresh-prices")
        live.assert_not_called()

    def test_a_rejected_sign_out_leaves_you_signed_in(self, csrf_client):
        csrf_client.post("/logout")
        assert csrf_client.get("/balance").status_code == 200

    def test_dev_login_needs_a_token_too(self, db, monkeypatch):
        """Login CSRF: a site could otherwise sign you in as someone else."""
        monkeypatch.setattr(config, "ALLOW_DEV_LOGIN", True)
        web_module.app.config.update(TESTING=True)
        monkeypatch.setitem(web_module.app.config, "WTF_CSRF_ENABLED", True)
        with web_module.app.test_client() as c:
            assert c.post("/login/dev", data={"email": "x@example.com"}).status_code == 400
            token = token_from(c, "/login")
            response = c.post("/login/dev", data={"email": "x@example.com", "csrf_token": token})
        assert response.status_code == 302
        assert users.get_by_email("x@example.com") is not None


class TestTheAskEndpoint:
    """JSON-only stops a plain HTML form, but not a script on another site
    that can send JSON with credentials. The token is what stops that."""

    def reply(self):
        candidate = SimpleNamespace(
            finish_reason=types.FinishReason.STOP,
            content=SimpleNamespace(parts=[SimpleNamespace(text="An answer.", thought=False)]),
        )
        return SimpleNamespace(prompt_feedback=None, candidates=[candidate])

    def fake_client(self):
        calls = []

        def generate(**kwargs):
            calls.append(kwargs)
            return self.reply()

        return SimpleNamespace(models=SimpleNamespace(generate_content=generate)), calls

    def test_json_without_a_token_is_refused_before_any_api_call(self, csrf_client):
        fake, calls = self.fake_client()
        with patch.object(assistant, "_get_client", return_value=fake):
            response = csrf_client.post("/api/assistant", json={"question": "Hi"})
        assert response.status_code == 400
        data = response.get_json()
        assert data["ok"] is False and data["kind"] == "expired"
        assert calls == []
        assert assistant_rows() == 0          # not counted against the limit

    def test_json_with_a_wrong_header_is_refused(self, csrf_client):
        response = csrf_client.post("/api/assistant", json={"question": "Hi"},
                                    headers={"X-CSRFToken": "forged"})
        assert response.status_code == 400

    def test_json_with_the_page_token_in_the_header_is_answered(self, csrf_client):
        page = text(csrf_client.get("/buy"))
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
        fake, calls = self.fake_client()
        with patch.object(assistant, "_get_client", return_value=fake):
            response = csrf_client.post("/api/assistant", json={"question": "Hi"},
                                        headers={"X-CSRFToken": token})
        assert response.status_code == 200 and response.get_json()["answer"] == "An answer."
        assert len(calls) == 1

    def test_the_panel_script_sends_the_token(self):
        js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
        assert '"X-CSRFToken": csrfToken()' in js
        assert 'meta[name="csrf-token"]' in js


class TestValidTokensWork:
    def test_buying_with_the_token_from_the_page(self, csrf_client, user):
        token = token_from(csrf_client)
        with quote():
            response = csrf_client.post("/buy/confirm", data={
                "symbol": "TST", "shares": "2", "csrf_token": token})
        assert response.status_code == 302
        assert portfolio.get_holding(user, "TST")[0] == Decimal("2")

    def test_signing_out_with_the_token(self, csrf_client):
        token = token_from(csrf_client)
        response = csrf_client.post("/logout", data={"csrf_token": token})
        assert response.status_code == 302
        assert csrf_client.get("/balance").status_code == 302

    def test_a_token_still_works_after_an_hour(self, csrf_client, user, monkeypatch):
        """Tokens last for the session, so a page left open is not refused."""
        import time as time_module
        token = token_from(csrf_client)
        real = time_module.time
        monkeypatch.setattr(time_module, "time", lambda: real() + 2 * 3600)
        with quote():
            response = csrf_client.post("/buy/confirm", data={
                "symbol": "TST", "shares": "1", "csrf_token": token})
        assert response.status_code == 302


class TestSignOut:
    def test_a_get_does_not_sign_you_out(self, csrf_client):
        response = csrf_client.get("/logout")
        assert response.status_code == 200
        assert "Sign out?" in text(response)
        assert csrf_client.get("/balance").status_code == 200

    def test_the_masthead_signs_out_with_a_post_form(self, csrf_client):
        body = text(csrf_client.get("/buy"))
        form = re.search(r'<form class="signout-form" method="post" action="/logout">(.*?)</form>',
                         body, re.DOTALL)
        assert form and TOKEN_RE.search(form.group(1))
        assert 'href="/logout"' not in body


class TestEveryFormCarriesAToken:
    def test_every_post_form_in_every_template(self):
        """Checked in the template source, so forms only rendered in some
        states (a failed lookup, dev login) are covered too."""
        checked = 0
        for path in glob.glob(os.path.join(ROOT, "templates", "*.html")):
            source = open(path, encoding="utf-8").read()
            for match in re.finditer(r'<form\b[^>]*method="post"[^>]*>(.*?)</form>', source, re.DOTALL):
                checked += 1
                assert 'name="csrf_token" value="{{ csrf_token() }}"' in match.group(1), (
                    f"a POST form in {os.path.basename(path)} has no CSRF token")
        assert checked >= 14

    @pytest.mark.parametrize("path", ["/", "/buy", "/actions", "/stock/TST", "/sell/TST", "/assistant"])
    def test_rendered_pages(self, csrf_client, user, path):
        own(user)
        with quote():
            body = text(csrf_client.get(path))
        forms = re.findall(r'<form\b[^>]*method="post"[^>]*>(.*?)</form>', body, re.DOTALL)
        assert forms, path
        for form in forms:
            assert TOKEN_RE.search(form), path

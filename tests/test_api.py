"""FastAPI tests.

Same ground rules as the Flask suite: market data is mocked, the database
is the throwaway test database from conftest, and Google is never called.
"""
import contextlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from api import security, settings
from api.main import app
from portfolio_tracker import config, operations
from portfolio_tracker.models import history, portfolio, stocks, trades, users, value_history
from portfolio_tracker.services import assistant, market_data

STARTING = Decimal("50000")


@contextlib.contextmanager
def live_price(price, name="Test Company Inc.", previous_close=None):
    value = Decimal(str(price)) if price is not None else None
    with patch.object(market_data, "get_live_price", return_value=value), \
            patch.object(market_data, "get_quote", return_value=(value, name, previous_close)):
        yield


@contextlib.contextmanager
def _patches(*patchers):
    with contextlib.ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        yield


def own(user_id, symbol, shares, price, name="Test Company Inc."):
    portfolio.record_purchase(user_id, symbol, name, Decimal(str(price)), Decimal(str(shares)))


@pytest.fixture
def api(db):
    """A signed-out API client."""
    with TestClient(app) as client:
        yield client


@pytest.fixture
def authed(user, api):
    """An API client carrying a valid token for the `user` fixture's account."""
    token, _ = security.issue_token(user)
    api.headers["Authorization"] = f"Bearer {token}"
    return api


# ------------------------------------------------------------------- auth

class TestTokens:
    @pytest.mark.parametrize("method, path", [
        ("get", "/api/auth/me"), ("get", "/api/portfolio"), ("get", "/api/quote?symbol=AAPL"),
        ("get", "/api/search?q=apple"), ("post", "/api/buy"), ("post", "/api/sell"),
        ("get", "/api/stocks/AAPL"), ("get", "/api/sell/AAPL"),
        ("get", "/api/trades"), ("get", "/api/history"),
        ("get", "/api/watchlist"), ("post", "/api/watchlist"),
        ("post", "/api/watchlist/refresh"), ("delete", "/api/watchlist/AAPL"),
        ("get", "/api/leaderboard"), ("get", "/api/account"),
        ("post", "/api/account/leaderboard"), ("get", "/api/account/export"),
        ("post", "/api/account/delete"),
        ("get", "/api/assistant/status"), ("post", "/api/assistant"),
        ("post", "/api/actions/refresh-prices"), ("post", "/api/actions/load-history"),
    ])
    def test_every_account_route_needs_a_token(self, api, method, path):
        response = getattr(api, method)(path)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_a_valid_token_names_the_account(self, authed, user):
        body = authed.get("/api/auth/me").json()
        assert body["id"] == user
        assert body["email"] == "tester@example.com"
        assert Decimal(body["cash"]) == STARTING

    def test_money_is_a_string_not_a_float(self, authed):
        """JSON numbers are floats to every browser; money must not be."""
        assert isinstance(authed.get("/api/auth/me").json()["cash"], str)

    def test_an_expired_token_is_refused(self, api, user):
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        token = jwt.encode({"sub": str(user), "iss": security.ISSUER, "iat": past,
                            "exp": past + timedelta(minutes=1)},
                           settings.jwt_secret_key(), algorithm="HS256")
        response = api.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    def test_a_token_signed_with_another_key_is_refused(self, api, user):
        now = datetime.now(timezone.utc)
        token = jwt.encode({"sub": str(user), "iss": security.ISSUER, "iat": now,
                            "exp": now + timedelta(hours=1)},
                           "a-different-key-that-is-long-enough-for-hs256", algorithm="HS256")
        response = api.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    def test_an_unsigned_token_is_refused(self, api, user):
        """alg=none must never be accepted, whatever the token claims."""
        now = datetime.now(timezone.utc)
        token = jwt.encode({"sub": str(user), "iss": security.ISSUER, "iat": now,
                            "exp": now + timedelta(hours=1)}, None, algorithm="none")
        response = api.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    def test_a_token_for_a_deleted_account_stops_working(self, authed, user):
        users.delete_account(user)
        response = authed.get("/api/auth/me")
        assert response.status_code == 401
        assert "no longer exists" in response.json()["detail"]

    def test_production_refuses_to_start_without_a_key(self, monkeypatch):
        monkeypatch.setattr(settings, "JWT_SECRET_KEY", None)
        monkeypatch.setattr(settings, "IS_PRODUCTION", True)
        with pytest.raises(config.ConfigurationError):
            settings.jwt_secret_key()


class TestAuthConfig:
    def test_reports_nothing_configured(self, api):
        body = api.get("/api/auth/config").json()
        assert body == {"google_client_id": None, "dev_login": False,
                        "starting_cash": "50000"}

    def test_reports_google_client_id_when_configured(self, api, monkeypatch):
        monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "client-123")
        monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "secret")
        assert api.get("/api/auth/config").json()["google_client_id"] == "client-123"


class TestGoogleSignIn:
    @pytest.fixture(autouse=True)
    def google_configured(self, monkeypatch):
        monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "client-123")
        monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "secret")

    def claims(self, sub="google-sub-9", email="new@example.com", name="New Person"):
        return patch.object(security, "verify_google_credential",
                            return_value={"sub": sub, "email": email, "name": name})

    def test_first_sign_in_creates_the_account(self, api):
        with self.claims():
            response = api.post("/api/auth/google", json={"credential": "id-token"})
        assert response.status_code == 200
        body = response.json()
        assert body["new_account"] is True
        assert body["user"]["email"] == "new@example.com"
        assert Decimal(body["user"]["cash"]) == STARTING
        # The token it hands back works.
        me = api.get("/api/auth/me",
                     headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.json()["email"] == "new@example.com"

    def test_returning_sign_in_reuses_the_account(self, api, user):
        with self.claims(sub="test-sub-1", email="tester@example.com"):
            body = api.post("/api/auth/google", json={"credential": "id-token"}).json()
        assert body["new_account"] is False
        assert body["user"]["id"] == user

    def test_a_rejected_credential_is_401(self, api):
        with patch.object(security, "verify_google_credential",
                          side_effect=ValueError("Token expired")):
            response = api.post("/api/auth/google", json={"credential": "bad"})
        assert response.status_code == 401
        assert "Token expired" not in response.text

    def test_a_credential_without_sub_is_refused(self, api):
        with self.claims(sub=None):
            response = api.post("/api/auth/google", json={"credential": "id-token"})
        assert response.status_code == 401

    def test_not_configured_is_404(self, api, monkeypatch):
        monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", None)
        response = api.post("/api/auth/google", json={"credential": "id-token"})
        assert response.status_code == 404


class TestDevSignIn:
    def test_off_by_default(self, api):
        assert api.post("/api/auth/dev", json={"email": "a@b.c"}).status_code == 404

    def test_signs_in_when_enabled(self, api, monkeypatch):
        monkeypatch.setattr(config, "ALLOW_DEV_LOGIN", True)
        body = api.post("/api/auth/dev", json={"email": " Dev@Example.com "}).json()
        assert body["user"]["email"] == "dev@example.com"
        assert body["new_account"] is True

    def test_reuses_an_existing_account_at_that_address(self, api, user, monkeypatch):
        monkeypatch.setattr(config, "ALLOW_DEV_LOGIN", True)
        body = api.post("/api/auth/dev", json={"email": "tester@example.com"}).json()
        assert body["user"]["id"] == user
        assert body["new_account"] is False


# --------------------------------------------------------------- portfolio

class TestPortfolio:
    def test_empty_account(self, authed):
        body = authed.get("/api/portfolio").json()
        assert body["holdings"] == []
        assert Decimal(body["cash"]) == STARTING
        assert Decimal(body["net_worth"]) == STARTING
        assert body["total_return"] == "0"
        assert body["performance"] is None

    def test_lists_holdings_with_gain(self, authed, seeded):
        own(seeded, "AAPL", 10, 150, name="Apple Inc.")
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("200.00"))
        body = authed.get("/api/portfolio").json()
        [row] = body["holdings"]
        assert row["symbol"] == "AAPL"
        assert Decimal(row["shares"]) == 10
        assert Decimal(row["value"]) == Decimal("2000")
        assert Decimal(row["gain_loss"]) == Decimal("500")
        assert Decimal(body["cash"]) == STARTING - 1500

    def test_one_account_never_sees_another(self, authed, seeded, other_user):
        own(other_user, "MSFT", 5, 400)
        assert authed.get("/api/portfolio").json()["holdings"] == []


# ------------------------------------------------------------------- trades

class TestTrades:
    def test_empty_account(self, authed):
        body = authed.get("/api/trades").json()
        assert body["trades"] == []
        assert (body["total"], body["page"], body["pages"]) == (0, 1, 1)
        assert Decimal(body["realized_total"]) == 0
        assert body["closed_positions"] == []

    def test_lists_newest_first_with_what_a_sale_earned(self, authed, seeded):
        own(seeded, "AAPL", 10, 150, name="Apple Inc.")
        portfolio.record_sale(seeded, "AAPL", Decimal("200"), Decimal("4"))
        body = authed.get("/api/trades").json()
        sold, bought = body["trades"]
        assert (sold["side"], bought["side"]) == ("sell", "buy")
        assert Decimal(sold["gain"]) == Decimal("200")
        assert bought["gain"] is None
        assert body["total"] == 2
        assert Decimal(body["realized_total"]) == Decimal("200")
        assert body["closed_positions"] == []

    def test_a_position_sold_in_full_is_listed_as_closed(self, authed, seeded):
        own(seeded, "AAPL", 10, 150)
        portfolio.record_sale(seeded, "AAPL", Decimal("140"), Decimal("10"))
        [closed] = authed.get("/api/trades").json()["closed_positions"]
        assert closed["symbol"] == "AAPL"
        assert Decimal(closed["realized"]) == Decimal("-100")
        assert Decimal(closed["proceeds"]) == Decimal("1400")

    def test_pages_and_clamps_a_page_past_the_end(self, authed, seeded):
        size = trades.PAGE_SIZE
        for _ in range(size + 1):
            own(seeded, "AAPL", 1, 150)
        first = authed.get("/api/trades").json()
        last = authed.get("/api/trades?page=99").json()
        assert (len(first["trades"]), first["page"], first["pages"]) == (size, 1, 2)
        assert (len(last["trades"]), last["page"]) == (1, 2)

    def test_a_malformed_page_is_refused(self, authed):
        assert authed.get("/api/trades?page=0").status_code == 422
        assert authed.get("/api/trades?page=abc").status_code == 422

    def test_one_account_never_sees_another(self, authed, seeded, other_user):
        own(other_user, "MSFT", 5, 400)
        body = authed.get("/api/trades").json()
        assert body["trades"] == [] and body["total"] == 0


# ------------------------------------------------------------------ history

def closes(symbol, *prices):
    """Store daily closes for `symbol`, the last one being yesterday's."""
    from portfolio_tracker.db import cursor

    with cursor(commit=True) as cur:
        for days_ago, close in enumerate(reversed(prices), start=1):
            cur.execute(
                "INSERT INTO price_history (symbol, date, close) VALUES (%s, %s, %s)",
                (symbol, date.today() - timedelta(days=days_ago), Decimal(str(close))),
            )


class TestHistory:
    def test_empty_account(self, authed):
        body = authed.get("/api/history").json()
        assert body["rows"] == []
        assert body["days"] == history.RECENT_DAYS

    def test_recent_range_for_each_holding(self, authed, seeded):
        own(seeded, "AAPL", 10, 150, name="Apple Inc.")
        closes("AAPL", 180, 190, 200)
        [row] = authed.get("/api/history").json()["rows"]
        assert (row["symbol"], row["company_name"]) == ("AAPL", "Apple Inc.")
        assert Decimal(row["average"]) == Decimal("190")
        assert Decimal(row["high"]) == Decimal("200")
        assert Decimal(row["low"]) == Decimal("180")

    def test_a_holding_without_stored_prices_has_blanks_not_zeros(self, authed, seeded):
        own(seeded, "AAPL", 10, 150)
        [row] = authed.get("/api/history").json()["rows"]
        assert (row["average"], row["high"], row["low"]) == (None, None, None)

    def test_only_what_this_account_holds(self, authed, seeded, other_user):
        own(other_user, "MSFT", 5, 400)
        closes("MSFT", 390, 410)
        assert authed.get("/api/history").json()["rows"] == []


# --------------------------------------------------------------- watchlist

class TestWatchlist:
    def test_empty(self, authed):
        assert authed.get("/api/watchlist").json() == {"rows": [], "prices_as_of": None}

    def test_follow_prices_the_ticker_and_lists_it(self, authed, user):
        with live_price(200, name="Apple Inc.", previous_close=Decimal("190")):
            body = authed.post("/api/watchlist", json={"symbol": "aapl"}).json()
        assert body["symbol"] == "AAPL" and body["changed"] is True
        [row] = authed.get("/api/watchlist").json()["rows"]
        assert (row["symbol"], row["company_name"], row["owned"]) == ("AAPL", "Apple Inc.", False)
        assert Decimal(row["todays_change"]) == Decimal("10")

    def test_following_twice_changes_nothing(self, authed, user):
        with live_price(200):
            authed.post("/api/watchlist", json={"symbol": "AAPL"})
            body = authed.post("/api/watchlist", json={"symbol": "AAPL"}).json()
        assert body["changed"] is False
        assert "already" in body["message"]
        assert len(authed.get("/api/watchlist").json()["rows"]) == 1

    def test_a_ticker_that_cannot_be_priced_is_refused(self, authed, user):
        with live_price(None):
            response = authed.post("/api/watchlist", json={"symbol": "NOPE"})
        assert response.status_code == 400
        assert authed.get("/api/watchlist").json()["rows"] == []

    def test_a_held_ticker_is_marked(self, authed, seeded):
        own(seeded, "AAPL", 1, 150)
        with live_price(200):
            authed.post("/api/watchlist", json={"symbol": "AAPL"})
        [row] = authed.get("/api/watchlist").json()["rows"]
        assert row["owned"] is True

    def test_unfollow(self, authed, user):
        with live_price(200):
            authed.post("/api/watchlist", json={"symbol": "AAPL"})
        body = authed.delete("/api/watchlist/aapl").json()
        assert body == {"symbol": "AAPL", "changed": True,
                        "message": "AAPL removed from your watchlist."}
        assert authed.get("/api/watchlist").json()["rows"] == []
        assert authed.delete("/api/watchlist/AAPL").json()["changed"] is False

    def test_refresh_requotes_without_sampling_the_account(self, authed, user):
        with live_price(200):
            authed.post("/api/watchlist", json={"symbol": "AAPL"})
        with live_price(210, previous_close=Decimal("200")):
            body = authed.post("/api/watchlist/refresh").json()
        assert (body["updated"], body["failed"], body["total"]) == (1, 0, 1)
        [row] = authed.get("/api/watchlist").json()["rows"]
        assert Decimal(row["price"]) == Decimal("210")
        # Watching is not owning: it must not create a valuation.
        assert authed.get("/api/leaderboard").json()["standing"] is None

    def test_refresh_with_nothing_followed(self, authed):
        body = authed.post("/api/watchlist/refresh").json()
        assert body["total"] == 0 and "Nothing" in body["message"]

    def test_one_account_never_sees_or_removes_anothers(self, authed, user, other_user):
        with live_price(200):
            operations.watch(other_user, "AAPL")
        assert authed.get("/api/watchlist").json()["rows"] == []
        assert authed.delete("/api/watchlist/AAPL").json()["changed"] is False
        assert [row.symbol for row in operations.watchlist_rows(other_user)] == ["AAPL"]


# ------------------------------------------------------------- leaderboard

def valued(user_id, total):
    """Record an account valuation, as a price refresh would."""
    value_history.record(user_id, Decimal(str(total)), 0)


class TestLeaderboard:
    def test_nobody_listed_and_not_yet_valued(self, authed):
        body = authed.get("/api/leaderboard").json()
        assert body["rows"] == [] and body["standing"] is None
        assert Decimal(body["starting_cash"]) == STARTING

    def test_own_standing_is_shown_before_joining(self, authed, user):
        valued(user, 55000)
        body = authed.get("/api/leaderboard").json()
        assert body["rows"] == []
        assert body["standing"]["opted_in"] is False
        assert body["standing"]["rank"] == 1
        assert Decimal(body["standing"]["percent_return"]) == Decimal("10")

    def test_joining_lists_a_chosen_name_and_nothing_personal(self, authed, user, other_user):
        valued(user, 55000)
        valued(other_user, 60000)
        operations.set_leaderboard_participation(other_user, True, name="Ahead")
        joined = authed.post("/api/account/leaderboard",
                             json={"opt_in": True, "name": " Racer "}).json()
        assert joined["opt_in"] is True and joined["name"] == "Racer"
        assert joined["standing"]["rank"] == 2

        response = authed.get("/api/leaderboard")
        assert [(r["rank"], r["name"], r["is_you"]) for r in response.json()["rows"]] == [
            (1, "Ahead", False), (2, "Racer", True)]
        assert set(response.json()["rows"][0]) == {
            "rank", "name", "total_value", "percent_return", "is_you"}
        assert "example.com" not in response.text

    @pytest.mark.parametrize("name", ["", "   ", "me@example.com", "x" * 25])
    def test_a_bad_name_is_refused_and_nothing_changes(self, authed, user, name):
        response = authed.post("/api/account/leaderboard", json={"opt_in": True, "name": name})
        assert response.status_code == 400
        assert users.get_leaderboard_settings(user)[0] is False

    def test_a_name_someone_else_uses_is_refused(self, authed, user, other_user):
        operations.set_leaderboard_participation(other_user, True, name="Racer")
        response = authed.post("/api/account/leaderboard", json={"opt_in": True, "name": "Racer"})
        assert response.status_code == 400

    def test_leaving_keeps_the_name_for_next_time(self, authed, user):
        valued(user, 50000)
        authed.post("/api/account/leaderboard", json={"opt_in": True, "name": "Racer"})
        left = authed.post("/api/account/leaderboard", json={"opt_in": False}).json()
        assert left["opt_in"] is False and left["name"] == "Racer"
        assert authed.get("/api/leaderboard").json()["rows"] == []


# ----------------------------------------------------------------- account

class TestAccount:
    def test_account_details(self, authed, seeded):
        own(seeded, "AAPL", 1, 150)
        body = authed.get("/api/account").json()
        assert body["email"] == "tester@example.com"
        assert body["display_name"] == "Tester"
        assert Decimal(body["cash"]) == STARTING - 150
        assert body["trade_count"] == 1
        assert body["delete_confirmation"] == operations.DELETE_CONFIRMATION
        assert body["leaderboard_opt_in"] is False and body["leaderboard_name"] is None
        assert body["leaderboard_name_max"] == operations.LEADERBOARD_NAME_MAX

    def test_export_is_a_download_of_this_accounts_data(self, authed, seeded, other_user):
        own(seeded, "AAPL", 2, 150, name="Apple Inc.")
        own(other_user, "MSFT", 5, 400)
        response = authed.get("/api/account/export")
        assert response.status_code == 200
        assert response.headers["Content-Disposition"].startswith(
            'attachment; filename="portfolio-tracker-export-')
        assert response.headers["Cache-Control"] == "no-store"
        body = response.json()
        assert body["account"]["email"] == "tester@example.com"
        assert [h["symbol"] for h in body["holdings"]] == ["AAPL"]
        assert [t["symbol"] for t in body["trades"]] == ["AAPL"]
        assert "MSFT" not in response.text and "other@example.com" not in response.text

    @pytest.mark.parametrize("typed", ["", "delete me", "yes"])
    def test_delete_needs_the_word_typed(self, authed, user, typed):
        response = authed.post("/api/account/delete", json={"confirmation": typed})
        assert response.status_code == 400
        assert "Nothing has been deleted" in response.json()["detail"]
        assert users.get_by_id(user) is not None

    def test_delete_erases_the_account_and_ends_the_sign_in(self, authed, seeded, other_user):
        own(seeded, "AAPL", 1, 150)
        response = authed.post("/api/account/delete", json={"confirmation": " delete "})
        assert response.json() == {"email": "tester@example.com"}
        assert users.get_by_id(seeded) is None
        assert authed.get("/api/account").status_code == 401
        assert users.get_by_id(other_user) is not None


# ----------------------------------------------------------------- actions

class TestActions:
    def test_nothing_held_says_so(self, authed):
        for path in ("/api/actions/refresh-prices", "/api/actions/load-history"):
            body = authed.post(path).json()
            assert (body["kind"], body["total"], body["outcomes"]) == ("notice", 0, [])
            assert "no holdings" in body["message"]

    def test_refresh_reprices_and_values_the_account(self, authed, seeded):
        own(seeded, "AAPL", 10, 150)
        with live_price(210):
            body = authed.post("/api/actions/refresh-prices").json()
        assert body["kind"] == "success"
        assert body["message"] == "Refreshed 1 of 1 price."
        assert [(o["symbol"], o["ok"]) for o in body["outcomes"]] == [("AAPL", True)]
        [row] = authed.get("/api/portfolio").json()["holdings"]
        assert Decimal(row["current_price"]) == Decimal("210")
        # A refresh is what gives the leaderboard something to rank.
        assert authed.get("/api/leaderboard").json()["standing"] is not None

    def test_a_refresh_that_fails_everywhere_is_an_error(self, authed, seeded):
        own(seeded, "AAPL", 10, 150)
        with live_price(None):
            body = authed.post("/api/actions/refresh-prices").json()
        assert body["kind"] == "error"
        assert [o["ok"] for o in body["outcomes"]] == [False]

    def test_load_history_reports_each_holding(self, authed, seeded):
        own(seeded, "AAPL", 10, 150)
        report = operations.HistoryReport(
            [operations.SymbolOutcome("AAPL", True, "3 new days")], 3, 1)
        with patch.object(operations, "load_all_history", return_value=report) as load:
            body = authed.post("/api/actions/load-history").json()
        load.assert_called_once_with(seeded)
        assert body["kind"] == "success"
        assert body["message"] == "Checked 1 holding and stored 3 new days of prices."

    def test_an_outage_does_not_read_as_nothing_new(self, authed, seeded):
        report = operations.HistoryReport(
            [operations.SymbolOutcome("AAPL", False, "could not be downloaded")], 0, 1)
        with patch.object(operations, "load_all_history", return_value=report):
            body = authed.post("/api/actions/load-history").json()
        assert body["kind"] == "error"
        assert "could not be downloaded" in body["message"]


# --------------------------------------------------------------- assistant

class TestAssistant:
    def test_status(self, authed):
        body = authed.get("/api/assistant/status").json()
        assert body["enabled"] == config.ASSISTANT_ENABLED
        assert body["max_question_chars"] == assistant.MAX_QUESTION_CHARS
        assert isinstance(body["research"], bool)

    def test_an_answer_carries_its_sources_and_suggestions_untouched(self, authed, seeded):
        answer = assistant.Answer(
            True, "Because of earnings.", "ok", "",
            sources=(assistant.Source("Report", "https://example.com/r", "example.com"),),
            suggestions_html="<style>.x{}</style><a href='https://google.com'>q</a>",
            searches=("apple earnings",),
        )
        with live_price(200), patch.object(assistant, "ask", return_value=answer) as ask:
            response = authed.post("/api/assistant", json={
                "question": "Why did it move?", "symbol": "AAPL",
                "earlier": [{"question": "q1", "answer": "a1"}]})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True and body["answer"] == "Because of earnings."
        assert body["sources"] == [
            {"title": "Report", "uri": "https://example.com/r", "domain": "example.com"}]
        assert body["search_suggestions"] == answer.suggestions_html
        assert body["searches"] == ["apple earnings"]
        context, question, earlier = ask.call_args.args
        assert question == "Why did it move?"
        assert earlier == [{"question": "q1", "answer": "a1"}]

    def test_the_context_is_this_accounts_and_built_on_the_server(self, authed, seeded, other_user):
        own(seeded, "AAPL", 10, 150)
        own(other_user, "MSFT", 5, 400)
        with patch.object(assistant, "ask", return_value=assistant.Answer(True, "x", "ok", "")) as ask:
            authed.post("/api/assistant", json={"question": "How spread out am I?"})
        context = ask.call_args.args[0]
        assert "AAPL" in context and "MSFT" not in context
        assert "tester@example.com" not in context

    @pytest.mark.parametrize("kind, status", [
        ("invalid", 400), ("unavailable", 503), ("disabled", 503), ("failed", 502)])
    def test_a_failure_keeps_the_body_and_sets_the_status(self, authed, kind, status):
        with patch.object(assistant, "ask",
                          return_value=assistant.Answer(False, "", kind, "Why it failed.")):
            response = authed.post("/api/assistant", json={"question": "anything"})
        assert response.status_code == status
        body = response.json()
        assert (body["ok"], body["kind"]) == (False, kind)
        assert body["message"] == body["detail"] == "Why it failed."

    def test_the_rate_limit_stops_the_question_before_it_costs_anything(self, authed, monkeypatch):
        from api.routers import assistant as router

        monkeypatch.setattr(router, "ASSISTANT_LIMIT", 1)
        with patch.object(assistant, "ask",
                          return_value=assistant.Answer(True, "x", "ok", "")) as ask:
            assert authed.post("/api/assistant", json={"question": "one"}).status_code == 200
            limited = authed.post("/api/assistant", json={"question": "two"})
        assert limited.status_code == 429
        assert ask.call_count == 1
        assert limited.json()["kind"] == "busy"
        assert 0 < limited.json()["retry_after"] <= router.ASSISTANT_WINDOW + 1
        assert limited.headers["Retry-After"] == str(limited.json()["retry_after"])


# ------------------------------------------------------------------- search

class TestSearch:
    def test_returns_catalogue_matches(self, authed):
        found = [{"symbol": "AAPL", "name": "Apple Inc."}]
        with patch.object(market_data, "search_assets", return_value=found) as search:
            response = authed.get("/api/search?q=apple&limit=5")
        assert response.json() == found
        search.assert_called_once_with("apple", limit=5)

    def test_blank_query_is_empty(self, authed):
        assert authed.get("/api/search?q=").json() == []


# ---------------------------------------------------------------------- buy

class TestBuy:
    def test_quote(self, authed):
        with live_price(100):
            body = authed.get("/api/quote?symbol=aapl").json()
        assert body["symbol"] == "AAPL"
        assert body["price"] == "100"
        assert body["affordable"] == "500.0000"
        assert body["owned"] is None

    def test_quote_for_unknown_ticker_is_400(self, authed):
        with live_price(None):
            response = authed.get("/api/quote?symbol=ZZZZ")
        assert response.status_code == 400
        assert "No market data found" in response.json()["detail"]

    def test_preview_writes_nothing(self, authed, user):
        with live_price(100):
            body = authed.post("/api/buy/preview",
                               json={"symbol": "AAPL", "shares": "3"}).json()
        assert body["cost"] == "300.00"
        assert body["cash_after"] == "49700.00"
        assert portfolio.get_holding(user, "AAPL") is None
        assert users.get_cash(user) == STARTING

    def test_preview_rejects_more_than_the_cash_covers(self, authed):
        with live_price(100):
            response = authed.post("/api/buy/preview",
                                   json={"symbol": "AAPL", "shares": "501"})
        assert response.status_code == 400
        assert "only have $50,000.00" in response.json()["detail"]

    @pytest.mark.parametrize("shares", ["0", "-1", "abc", "NaN", ""])
    def test_preview_rejects_bad_quantities(self, authed, shares):
        with live_price(100):
            response = authed.post("/api/buy/preview",
                                   json={"symbol": "AAPL", "shares": shares})
        assert response.status_code == 400

    def test_a_float_quantity_is_refused_by_the_schema(self, authed):
        with live_price(100):
            response = authed.post("/api/buy", json={"symbol": "AAPL", "shares": 1.5})
        assert response.status_code == 422

    def test_buy_moves_cash_and_shares(self, authed, user):
        with live_price(100, name="Apple Inc."):
            response = authed.post("/api/buy", json={"symbol": "aapl", "shares": "2.5"})
        assert response.status_code == 200
        body = response.json()
        assert body["symbol"] == "AAPL"
        assert body["cost"] == "250.00"
        assert body["message"].startswith("Bought 2.5 shares of AAPL at $100.00 each.")
        assert portfolio.get_holding(user, "AAPL")[0] == Decimal("2.5")
        assert users.get_cash(user) == STARTING - 250

    def test_buy_accepts_an_integer_quantity(self, authed, user):
        with live_price(100):
            response = authed.post("/api/buy", json={"symbol": "AAPL", "shares": 1})
        assert response.status_code == 200
        assert response.json()["message"].startswith("Bought 1 share of AAPL")

    def test_market_data_outage_is_a_readable_error(self, authed):
        with patch.object(market_data, "get_quote",
                          side_effect=market_data.MarketDataUnavailable("AAPL")):
            response = authed.post("/api/buy", json={"symbol": "AAPL", "shares": "1"})
        assert response.status_code == 400
        assert "try again in a minute" in response.json()["detail"]


# --------------------------------------------------------------------- sell

class TestSell:
    def test_sell_quote(self, authed, seeded):
        own(seeded, "AAPL", 10, 150)
        with live_price(200):
            body = authed.get("/api/sell/aapl").json()
        assert body["symbol"] == "AAPL"
        assert Decimal(body["owned"]) == 10
        assert body["maximum"] == "10.0000"
        assert body["price"] == "200"

    def test_sell_quote_for_something_not_owned(self, authed):
        response = authed.get("/api/sell/AAPL")
        assert response.status_code == 400
        assert response.json()["detail"] == "You do not own any AAPL."

    def test_preview_writes_nothing(self, authed, seeded):
        own(seeded, "AAPL", 10, 150)
        with live_price(200):
            body = authed.post("/api/sell/preview",
                               json={"symbol": "AAPL", "shares": "4"}).json()
        assert body["proceeds"] == "800.00"
        assert body["gain"] == "200.00"
        assert body["closes"] is False
        assert portfolio.get_holding(seeded, "AAPL")[0] == 10

    def test_preview_refuses_an_oversell(self, authed, seeded):
        own(seeded, "AAPL", 10, 150)
        with live_price(200):
            response = authed.post("/api/sell/preview",
                                   json={"symbol": "AAPL", "shares": "11"})
        assert response.status_code == 400

    def test_selling_everything_closes_the_position(self, authed, seeded):
        own(seeded, "AAPL", 10, 150)
        with live_price(200):
            body = authed.post("/api/sell", json={"symbol": "AAPL", "shares": "10"}).json()
        assert body["closed"] is True
        assert body["proceeds"] == "2000.00"
        assert "position is now closed" in body["message"]
        assert portfolio.get_holding(seeded, "AAPL") is None
        assert users.get_cash(seeded) == STARTING - 1500 + 2000

    def test_partial_sale(self, authed, seeded):
        own(seeded, "AAPL", 10, 150)
        with live_price(200):
            body = authed.post("/api/sell", json={"symbol": "AAPL", "shares": "4"}).json()
        assert body["closed"] is False
        assert "You still own 6 shares." in body["message"]

    def test_one_share_left_is_singular(self, authed, seeded):
        own(seeded, "AAPL", 2, 150)
        with live_price(200):
            body = authed.post("/api/sell", json={"symbol": "AAPL", "shares": "1"}).json()
        assert "You still own 1 share." in body["message"]

    def test_cannot_sell_another_accounts_shares(self, authed, other_user, seeded):
        own(other_user, "AAPL", 10, 150)
        with live_price(200):
            response = authed.post("/api/sell", json={"symbol": "AAPL", "shares": "1"})
        assert response.status_code == 400
        assert portfolio.get_holding(other_user, "AAPL")[0] == 10


# ------------------------------------------------------------------- stocks

class TestStockDetail:
    def stored(self, closes):
        """Stand in for the stored daily series, covering the whole range."""
        start = date.today() - timedelta(days=len(closes) - 1)
        series = [(start + timedelta(days=i), Decimal(str(c))) for i, c in enumerate(closes)]
        return _patches(
            patch.object(history, "get_series", return_value=series),
            patch.object(stocks, "full_history_loaded", return_value=True),
        )

    def test_no_history_asks_for_a_fetch(self, authed):
        with live_price(200, name="Apple Inc."):
            body = authed.get("/api/stocks/aapl?range=1m").json()
        assert body["symbol"] == "AAPL"
        assert body["quote"]["company_name"] == "Apple Inc."
        assert body["chart"] is None
        assert body["needs_fetch"] is True
        assert body["range"] == "1m"
        assert [r["key"] for r in body["ranges"]] == ["1d", "5d", "1m", "3m", "6m", "1y", "all"]

    def test_unknown_range_falls_back_quietly(self, authed):
        with live_price(200):
            body = authed.get("/api/stocks/AAPL?range=nonsense").json()
        assert body["range"] in {"1d", "5d", "1m", "3m", "6m", "1y", "all"}

    def test_draws_stored_history(self, authed):
        with self.stored([100, 110, 105, 120]), live_price(120):
            body = authed.get("/api/stocks/AAPL?range=1m").json()
        chart = body["chart"]
        assert chart["path"].startswith("M ")
        assert Decimal(chart["low"]) == 100
        assert Decimal(chart["high"]) == 120
        assert chart["readouts"][-1]["price"] == "$120.00"
        assert Decimal(body["stats"]["average"]) == Decimal("108.75")

    def test_unknown_ticker_is_400(self, authed):
        with live_price(None):
            assert authed.get("/api/stocks/ZZZZ").status_code == 400

    def test_fetch_loads_history(self, authed):
        with live_price(200, name="Apple Inc."), \
                patch.object(history, "load_full_history_for_symbol", return_value=42) as load:
            body = authed.post("/api/stocks/aapl/fetch", json={"range": "1y"}).json()
        load.assert_called_once_with("AAPL")
        assert body == {"symbol": "AAPL", "range": "1y", "added": 42,
                        "message": "Fetched 42 new days for AAPL."}

    def test_fetch_during_an_outage_is_503(self, authed):
        with live_price(200), patch.object(
                history, "load_full_history_for_symbol",
                side_effect=market_data.MarketDataUnavailable("AAPL")):
            response = authed.post("/api/stocks/AAPL/fetch", json={"range": "1y"})
        assert response.status_code == 503


# -------------------------------------------------------------------- misc

class TestPlumbing:
    def test_healthz(self, api):
        assert api.get("/api/healthz").json() == {"status": "ok"}

    def test_security_headers(self, api):
        response = api.get("/api/healthz")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cache-Control"] == "no-store"

    def test_unexpected_errors_hide_their_text(self, authed):
        with patch("portfolio_tracker.operations.account_summary",
                   side_effect=RuntimeError("secret internals")):
            client = TestClient(app, raise_server_exceptions=False)
            client.headers.update(authed.headers)
            response = client.get("/api/portfolio")
        assert response.status_code == 500
        assert "secret internals" not in response.text

    def test_malformed_body_names_the_field(self, authed):
        response = authed.post("/api/buy", json={"symbol": "AAPL"})
        assert response.status_code == 422
        assert response.json()["fields"] == ["shares"]


# ------------------------------------------------------------- the web UI

class TestServingTheUi:
    """api/spa.py: the built Angular app, served beside the API in production."""

    @pytest.fixture
    def site(self, tmp_path):
        from fastapi import FastAPI

        from api import spa

        build = tmp_path / "browser"
        (build / "fonts").mkdir(parents=True)
        (build / "index.html").write_text("<!doctype html><app-root></app-root>")
        (build / "main-TMAFEFHT.js").write_text("console.log(1)")
        (build / "fonts" / "plex.woff2").write_bytes(b"font")
        (tmp_path / "secret.txt").write_text("outside the build")

        site = FastAPI()

        @site.get("/api/healthz")
        def healthz():
            return {"status": "ok"}

        assert spa.mount_spa(site, str(build)) is True
        return TestClient(site)

    def test_nothing_is_mounted_without_a_build(self, tmp_path):
        from fastapi import FastAPI

        from api import spa

        assert spa.mount_spa(FastAPI(), str(tmp_path / "missing")) is False

    @pytest.mark.parametrize("path", ["/", "/login", "/stock/AAPL", "/account/deleted"])
    def test_every_page_address_gets_the_app(self, site, path):
        response = site.get(path)
        assert response.status_code == 200
        assert "<app-root>" in response.text
        assert "immutable" not in response.headers.get("Cache-Control", "")

    def test_a_ticker_with_a_dot_is_still_a_page(self, site):
        # BRK.B looks like a file name. It must not 404.
        assert "<app-root>" in site.get("/stock/BRK.B").text

    def test_hashed_bundles_are_cached_forever_and_other_files_are_not(self, site):
        bundle = site.get("/main-TMAFEFHT.js")
        assert bundle.text == "console.log(1)"
        assert "immutable" in bundle.headers["Cache-Control"]
        font = site.get("/fonts/plex.woff2")
        assert font.content == b"font"
        assert "immutable" not in font.headers.get("Cache-Control", "")

    def test_the_api_still_answers_first(self, site):
        assert site.get("/api/healthz").json() == {"status": "ok"}

    def test_an_unknown_api_path_is_a_json_404_not_a_page(self, site):
        for path in ("/api/nope", "/api"):
            response = site.get(path)
            assert response.status_code == 404
            assert response.json() == {"detail": "Not found"}

    def test_a_missing_asset_is_a_404_not_a_page(self, site):
        assert site.get("/main-GONE0000.js").status_code == 404

    @pytest.mark.parametrize("path", [
        "/../secret.txt", "/%2e%2e/secret.txt", "/fonts/../../secret.txt",
        "/..%2fsecret.txt", "/%2e%2e%2fsecret.txt"])
    def test_nothing_outside_the_build_is_ever_served(self, site, path):
        assert "outside the build" not in site.get(path).text

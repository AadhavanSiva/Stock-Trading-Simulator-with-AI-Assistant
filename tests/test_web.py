"""Flask front-end tests.

Market data is mocked, so these run offline. The database fixtures come
from conftest and point at the throwaway test database.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest

import web as web_module
from portfolio_tracker.models import portfolio, users
from portfolio_tracker.services import market_data

STARTING = Decimal("50000")


@pytest.fixture
def anon(db):
    """A client with no session — signed out."""
    web_module.app.config.update(TESTING=True)
    with web_module.app.test_client() as c:
        yield c


@pytest.fixture
def client(user):
    """A client already signed in as the `user` fixture's account."""
    web_module.app.config.update(TESTING=True)
    with web_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = user
        yield c


def quote(price, name="Test Company Inc."):
    value = Decimal(str(price)) if price is not None else None
    return patch.object(market_data, "get_quote", return_value=(value, name))


def live_price(price):
    value = Decimal(str(price)) if price is not None else None
    return patch.object(market_data, "get_live_price", return_value=value)


def own(user_id, symbol, shares, price, name="Test Company Inc."):
    """Put a position on the books without going through the web layer."""
    portfolio.record_purchase(
        user_id, symbol, name, Decimal(str(price)), Decimal(str(shares))
    )


def text(response):
    return response.get_data(as_text=True)


class TestAuthGate:
    @pytest.mark.parametrize("path", [
        "/", "/balance", "/buy", "/sell", "/history", "/actions",
    ])
    def test_pages_require_signing_in(self, anon, path):
        response = anon.get(path)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    @pytest.mark.parametrize("path", [
        "/buy/lookup", "/buy/review", "/buy/confirm",
        "/actions/refresh-prices", "/actions/load-history",
    ])
    def test_write_endpoints_require_signing_in(self, anon, path):
        response = anon.post(path, data={"symbol": "TST", "shares": "1"})
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_signed_out_post_writes_nothing(self, anon, user):
        with quote("100"):
            anon.post("/buy/confirm", data={"symbol": "TST", "shares": "1"})
        assert portfolio.get_holding(user, "TST") is None

    def test_login_page_renders(self, anon):
        body = text(anon.get("/login"))
        assert "Practice investing" in body

    def test_login_page_explains_setup_when_google_is_unconfigured(self, anon):
        body = text(anon.get("/login"))
        assert "console.cloud.google.com" in body
        assert "/auth/callback" in body

    def test_dev_login_is_off_by_default(self, anon):
        assert anon.post("/login/dev", data={"email": "x@y.com"}).status_code == 404

    def test_logout_clears_the_session(self, client):
        client.get("/logout")
        assert client.get("/").status_code == 302

    def test_session_pointing_at_a_deleted_account_is_cleared(self, client, user):
        from portfolio_tracker.db import cursor
        with cursor(commit=True) as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user,))
        response = client.get("/", follow_redirects=True)
        assert "no longer exists" in text(response)


class TestAccountIsolation:
    def test_you_cannot_see_another_account_holdings(self, client, other_user):
        own(other_user, "SECRET", 10, 100, name="Their Company")
        body = text(client.get("/"))
        assert "SECRET" not in body
        assert "Their Company" not in body

    def test_you_cannot_sell_another_account_holding(self, client, other_user):
        own(other_user, "SECRET", 10, 100)
        with quote("150"), live_price("150"):
            client.post("/sell/SECRET/confirm", data={"shares": "10"},
                        follow_redirects=True)
        assert portfolio.get_holding(other_user, "SECRET")[0] == Decimal("10")

    def test_their_cash_is_untouched_by_your_trades(self, client, user, other_user):
        with quote("100"):
            client.post("/buy/confirm", data={"symbol": "TST", "shares": "10"})
        assert users.get_cash(user) == STARTING - Decimal("1000")
        assert users.get_cash(other_user) == STARTING


class TestBalancePage:
    def test_shows_cash_holdings_and_total(self, client, user):
        own(user, "AAPL", 10, 100, name="Apple Inc.")
        with live_price("150"):
            client.post("/actions/refresh-prices")

        body = text(client.get("/balance"))
        assert "$49,000.00" in body   # cash
        assert "$1,500.00" in body    # holdings at 150
        assert "$50,500.00" in body   # total

    def test_all_cash_empty_state(self, client):
        body = text(client.get("/balance"))
        assert "All of it is still cash" in body
        assert "$50,000.00" in body

    def test_cash_appears_in_the_header_on_every_page(self, client):
        for path in ("/", "/buy", "/sell", "/history", "/actions", "/balance"):
            assert "$50,000.00" in text(client.get(path)), path


class TestPortfolioPage:
    def test_empty_state_mentions_the_starting_cash(self, client):
        body = text(client.get("/"))
        assert "Your portfolio is empty" in body
        assert "$50,000.00" in body

    def test_lists_holdings_with_company_name(self, client, user):
        own(user, "AAPL", 10, 100, name="Apple Inc.")
        body = text(client.get("/"))
        assert "AAPL" in body
        assert "Apple Inc." in body

    def test_shows_gain_with_sign_and_word_not_only_colour(self, client, user):
        own(user, "AAPL", 10, 100, name="Apple Inc.")
        with live_price("150"):
            client.post("/actions/refresh-prices")
        body = text(client.get("/"))
        assert "+500.00" in body
        assert ">up<" in body
        assert "▲" in body

    def test_shows_loss_with_its_own_label(self, client, user):
        own(user, "AAPL", 10, 100)
        with live_price("50"):
            client.post("/actions/refresh-prices")
        body = text(client.get("/"))
        assert "-500.00" in body
        assert ">down<" in body


class TestBuyFlow:
    def test_lookup_shows_name_price_and_buying_power(self, client):
        with quote("150.25", "Test Company Inc."):
            body = text(client.post("/buy/lookup", data={"symbol": "tst"}))
        assert "Test Company Inc." in body
        assert "$150.25" in body
        assert "332.7787" in body   # 50000 / 150.25, truncated

    def test_unknown_ticker_is_rejected_clearly(self, client):
        with quote(None):
            response = client.post("/buy/lookup", data={"symbol": "BOGUS"})
        assert response.status_code == 400
        assert "No market data found" in text(response)

    def test_review_shows_cost_and_cash_afterwards(self, client):
        with quote("100"):
            body = text(client.post("/buy/review", data={"symbol": "TST", "shares": "3"}))
        assert "$300.00" in body
        assert "$49,700.00" in body

    def test_review_does_not_write_anything(self, client, user):
        with quote("100"):
            client.post("/buy/review", data={"symbol": "TST", "shares": "3"})
        assert portfolio.get_holding(user, "TST") is None
        assert users.get_cash(user) == STARTING

    def test_confirm_executes_and_debits_cash(self, client, user):
        with quote("150.25"):
            response = client.post(
                "/buy/confirm", data={"symbol": "TST", "shares": "4"},
                follow_redirects=True,
            )
        assert portfolio.get_holding(user, "TST") == (Decimal("4"), Decimal("150.25"))
        assert users.get_cash(user) == STARTING - Decimal("601.00")
        assert "Bought 4 shares" in text(response)

    def test_cannot_buy_beyond_the_cash_balance(self, client, user):
        with quote("100"):
            response = client.post(
                "/buy/review", data={"symbol": "TST", "shares": "1000"}
            )
        assert response.status_code == 400
        assert "only have" in text(response)
        assert portfolio.get_holding(user, "TST") is None

    def test_overspend_posted_straight_to_confirm_is_refused(self, client, user):
        """Skipping review must not skip the affordability check."""
        with quote("100"):
            client.post("/buy/confirm", data={"symbol": "TST", "shares": "1000"},
                        follow_redirects=True)
        assert portfolio.get_holding(user, "TST") is None
        assert users.get_cash(user) == STARTING

    def test_user_cannot_set_their_own_purchase_price(self, client, user):
        with quote("150.25"):
            client.post(
                "/buy/confirm",
                data={"symbol": "TST", "shares": "1", "price": "1.00",
                      "purchase_price": "1.00"},
            )
        _, cost = portfolio.get_holding(user, "TST")
        assert cost == Decimal("150.25")

    def test_user_cannot_top_up_their_own_cash(self, client, user):
        """A posted cash field must be ignored."""
        with quote("100"):
            client.post("/buy/confirm",
                        data={"symbol": "TST", "shares": "1", "cash": "999999"})
        assert users.get_cash(user) == STARTING - Decimal("100")

    @pytest.mark.parametrize("bad", ["0", "-5", "abc", "", "nan", "Infinity"])
    def test_invalid_quantity_is_refused_server_side(self, client, user, bad):
        with quote("100"):
            response = client.post("/buy/review", data={"symbol": "TST", "shares": bad})
        assert response.status_code == 400
        assert portfolio.get_holding(user, "TST") is None

    @pytest.mark.parametrize("bad", ["0", "-5", "abc", "nan"])
    def test_invalid_quantity_cannot_be_posted_straight_to_confirm(self, client, user, bad):
        with quote("100"):
            client.post("/buy/confirm", data={"symbol": "TST", "shares": bad},
                        follow_redirects=True)
        assert portfolio.get_holding(user, "TST") is None


class TestSellFlow:
    @pytest.fixture
    def holder(self, client, user):
        own(user, "TST", 10, 100)
        return client

    def test_empty_state_when_nothing_owned(self, client):
        assert "Nothing to sell yet" in text(client.get("/sell"))

    def test_quantity_page_shows_price_and_amount_owned(self, holder):
        with quote("150"):
            body = text(holder.get("/sell/TST"))
        assert "$150.00" in body
        assert "10" in body

    def test_maximum_is_truncated_not_rounded(self, client, user):
        """1.99999 must display as 1.9999 — a rounded 2.0 would be rejected."""
        own(user, "TST", "1.99999", 100)
        with quote("150"):
            body = text(client.get("/sell/TST"))
        assert "1.9999" in body
        assert "1.99999" not in body

    def test_review_shows_proceeds_and_cash_afterwards(self, holder):
        with quote("150"):
            body = text(holder.post("/sell/TST/review", data={"shares": "4"}))
        assert "$600.00" in body
        assert "$49,600.00" in body   # 49,000 cash + 600 proceeds

    def test_review_does_not_sell_anything(self, holder, user):
        with quote("150"):
            holder.post("/sell/TST/review", data={"shares": "4"})
        assert portfolio.get_holding(user, "TST")[0] == Decimal("10")

    def test_partial_sale_credits_cash(self, holder, user):
        with quote("150"), live_price("150"):
            response = holder.post("/sell/TST/confirm", data={"shares": "4"},
                                   follow_redirects=True)
        assert portfolio.get_holding(user, "TST")[0] == Decimal("6")
        assert users.get_cash(user) == STARTING - Decimal("1000") + Decimal("600")
        assert "Sold 4 shares" in text(response)

    def test_selling_everything_closes_the_position(self, holder, user):
        with quote("150"), live_price("150"):
            response = holder.post("/sell/TST/confirm", data={"shares": "10"},
                                   follow_redirects=True)
        assert portfolio.get_holding(user, "TST") is None
        assert "closed" in text(response)

    def test_cannot_oversell(self, holder, user):
        with quote("150"), live_price("150"):
            response = holder.post("/sell/TST/review", data={"shares": "11"})
        assert response.status_code == 400
        assert "You only own" in text(response)
        assert portfolio.get_holding(user, "TST")[0] == Decimal("10")

    def test_oversell_posted_straight_to_confirm_is_refused(self, holder, user):
        with quote("150"), live_price("150"):
            holder.post("/sell/TST/confirm", data={"shares": "9999"},
                        follow_redirects=True)
        assert portfolio.get_holding(user, "TST")[0] == Decimal("10")

    def test_selling_something_not_owned(self, client):
        response = client.get("/sell/NOPE", follow_redirects=True)
        assert "do not own any NOPE" in text(response)

    @pytest.mark.parametrize("bad", ["0", "-5", "abc", "nan"])
    def test_invalid_quantity_is_refused(self, holder, user, bad):
        with quote("150"), live_price("150"):
            holder.post("/sell/TST/confirm", data={"shares": bad},
                        follow_redirects=True)
        assert portfolio.get_holding(user, "TST")[0] == Decimal("10")


class TestHistoryPage:
    def test_empty_state(self, client):
        assert "No price history yet" in text(client.get("/history"))

    def test_lists_holdings(self, client, user):
        own(user, "AAPL", 10, 100, name="Apple Inc.")
        body = text(client.get("/history"))
        assert "Apple Inc." in body
        assert "30-day average" in body


class TestActions:
    def test_page_renders(self, client):
        assert "Refresh prices" in text(client.get("/actions"))

    def test_refresh_reports_what_happened(self, client, user):
        own(user, "AAPL", 10, 100)
        with live_price("150"):
            body = text(client.post("/actions/refresh-prices"))
        assert "Refreshed 1 of 1" in body
        assert "$150.00" in body

    def test_refresh_reports_failures_without_stopping(self, client, user):
        own(user, "AAPL", 10, 100)
        own(user, "BBB", 5, 50)
        with patch.object(market_data, "get_live_price",
                          side_effect=[Decimal("150"), None]):
            body = text(client.post("/actions/refresh-prices"))
        assert "Refreshed 1 of 2" in body
        assert "1 could not be updated" in body

    def test_refresh_with_no_holdings(self, client):
        response = client.post("/actions/refresh-prices", follow_redirects=True)
        assert "no holdings yet" in text(response).lower()

    def test_history_load_reports_row_count(self, client, user):
        own(user, "AAPL", 10, 100)
        with patch("portfolio_tracker.models.history.load_history_for_symbol",
                   return_value=42):
            body = text(client.post("/actions/load-history"))
        assert "42 new rows" in body


class TestSafety:
    def test_unknown_page_renders_a_friendly_404(self, client):
        response = client.get("/no-such-page")
        assert response.status_code == 404
        assert "does not exist" in text(response)

    def test_ticker_is_normalised_to_upper_case(self, client, user):
        with quote("100"):
            client.post("/buy/confirm", data={"symbol": "  tst  ", "shares": "1"})
        assert portfolio.get_holding(user, "TST") is not None

    def test_no_secret_key_is_hardcoded(self):
        import inspect

        from portfolio_tracker import config

        source = inspect.getsource(config)
        assert "FLASK_SECRET_KEY" in source
        assert "token_hex" in source

    def test_google_credentials_come_from_the_environment(self):
        import inspect

        from portfolio_tracker import config

        source = inspect.getsource(config)
        assert 'os.getenv("GOOGLE_CLIENT_ID")' in source
        assert 'os.getenv("GOOGLE_CLIENT_SECRET")' in source

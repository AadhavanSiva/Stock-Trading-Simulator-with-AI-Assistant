"""Production error handling: friendly pages, real errors in the log only."""
from decimal import Decimal
from unittest.mock import patch

import pytest

import web as web_module
from portfolio_tracker import operations
from portfolio_tracker.models import portfolio, stocks
from portfolio_tracker.services import market_data

SECRET = "HTTPSConnectionPool(host='data.alpaca.markets'): internal-detail-42"


def text(response):
    return response.get_data(as_text=True)


@pytest.fixture
def production_errors(monkeypatch):
    """Handle exceptions as a production server does, instead of letting
    the test client re-raise them."""
    monkeypatch.setitem(web_module.app.config, "PROPAGATE_EXCEPTIONS", False)


def provider_down():
    """The market data provider failing the way a real outage does.

    Raised from the transport, below every bit of parsing, so the test
    exercises the same path a DNS failure or a dropped connection takes —
    and proves the underlying text is not carried up to the page.
    """
    return patch.object(market_data._session, "get",
                        side_effect=RuntimeError(SECRET))


def assert_hidden(body):
    assert SECRET not in body
    assert "internal-detail-42" not in body
    assert "Traceback" not in body
    assert "RuntimeError" not in body


class TestUnhandledErrors:
    def test_a_page_error_shows_the_friendly_500_page(self, client, production_errors, caplog):
        with patch.object(operations, "account_summary", side_effect=RuntimeError(SECRET)):
            response = client.get("/balance")
        body = text(response)
        assert response.status_code == 500
        assert "Something went wrong" in body
        assert "Try again" in body and 'href="/balance"' in body
        assert "Skip to main content" in body            # the site's own layout
        assert_hidden(body)
        assert SECRET in caplog.text                      # but the log has it

    def test_an_api_error_answers_in_json(self, client, production_errors, caplog):
        with patch.object(operations, "look_up", side_effect=RuntimeError(SECRET)):
            response = client.get("/api/quote?symbol=AAPL")
        assert response.status_code == 500
        data = response.get_json()
        assert data["ok"] is False and data["kind"] == "failed"
        assert_hidden(response.get_data(as_text=True))
        assert SECRET in caplog.text

    def test_a_failed_post_offers_no_retry_link(self, client, production_errors):
        """Re-requesting a POST by link would be a GET to a POST-only route."""
        with patch.object(operations, "refresh_prices", side_effect=RuntimeError(SECRET)):
            body = text(client.post("/actions/refresh-prices"))
        assert "Something went wrong" in body
        assert ">Try again<" not in body

    def test_not_found_still_uses_its_own_page(self, client):
        response = client.get("/no-such-page")
        assert response.status_code == 404
        assert "That page does not exist." in text(response)


class TestProviderErrorTextNeverReachesAPerson:
    def test_ticker_lookup(self, client, caplog):
        with provider_down():
            response = client.get("/api/quote?symbol=AAPL")
        data = response.get_json()
        assert response.status_code == 400
        assert data["error"].startswith("Could not look up AAPL right now.")
        assert_hidden(response.get_data(as_text=True))
        assert SECRET in caplog.text

    def test_buy_flow(self, client):
        with provider_down():
            body = text(client.post("/buy/lookup", data={"symbol": "AAPL"}))
        assert "Could not look up AAPL right now." in body
        assert_hidden(body)

    def test_stock_page(self, client):
        with provider_down():
            body = text(client.get("/stock/AAPL", follow_redirects=True))
        assert "Could not look up AAPL right now." in body
        assert_hidden(body)

    def test_sale(self, client, user):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        portfolio.record_purchase(user, "AAPL", "Apple Inc.", Decimal("100"), Decimal("2"))
        with provider_down():
            body = text(client.post("/sell/AAPL/confirm", data={"shares": "1"}, follow_redirects=True))
        assert "Could not look up AAPL right now." in body
        assert_hidden(body)
        assert portfolio.get_holding(user, "AAPL")[0] == Decimal("2")

    def test_price_refresh_report(self, client, user):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        portfolio.record_purchase(user, "AAPL", "Apple Inc.", Decimal("100"), Decimal("2"))
        with provider_down():
            body = text(client.post("/actions/refresh-prices"))
        assert "the market data service didn&#39;t respond" in body
        assert_hidden(body)

    def test_history_load_report(self, client, user):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        portfolio.record_purchase(user, "AAPL", "Apple Inc.", Decimal("100"), Decimal("2"))
        with provider_down():
            body = text(client.post("/actions/load-history"))
        assert_hidden(body)
        # Regression: an outage used to read as "stored 0 new days" and nothing else.
        assert "1 could not be downloaded, so it was not checked." in body
        assert "flash-error" in body

    def test_chart_fetch(self, client):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        with patch.object(market_data, "get_quote",
                          return_value=(Decimal("100"), "Apple Inc.", None)), provider_down():
            body = text(client.post("/stock/AAPL/fetch", data={"range": "1y"}, follow_redirects=True))
        assert "Could not fetch prices for AAPL. The market data service" in body
        assert "Fetched 0" not in body
        assert_hidden(body)

    def test_google_sign_in_failure(self, anon, monkeypatch):
        from types import SimpleNamespace
        from portfolio_tracker import config
        monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "id")
        monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "secret")
        failing = SimpleNamespace(authorize_access_token=lambda: (_ for _ in ()).throw(RuntimeError(SECRET)))
        with patch.object(web_module.oauth, "google", failing, create=True):
            body = text(anon.get("/auth/callback", follow_redirects=True))
        assert "Google sign-in failed. Please try signing in again." in body
        assert_hidden(body)

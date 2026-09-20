"""Today's change: what a position has done since the previous close."""
from decimal import Decimal
from unittest.mock import patch

import pytest

from portfolio_tracker import operations
from portfolio_tracker.db import cursor
from portfolio_tracker.models import portfolio, stocks
from portfolio_tracker.services import market_data


@pytest.fixture
def seeded(user):
    stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"), previous_close=Decimal("90"))
    stocks.upsert_stock("MSFT", "Microsoft Corp.", Decimal("200"), previous_close=Decimal("220"))
    return user


def hold(user_id, symbol, shares, price):
    portfolio.record_purchase(user_id, symbol, symbol, Decimal(price), Decimal(shares))


class TestStoringThePreviousClose:
    def test_a_quote_reports_one(self):
        info = {"currentPrice": 100.0, "longName": "Test", "regularMarketPreviousClose": 95.0}
        with patch.object(market_data, "_fetch_quote",
                          return_value=(Decimal("100"), "Test", Decimal("95"))):
            assert market_data.get_quote("X") == (Decimal("100"), "Test", Decimal("95"))
        assert info["regularMarketPreviousClose"] == 95.0   # the field we read

    def test_the_older_spelling_is_accepted(self):
        """Yahoo still returns `previousClose` on some symbols."""
        class Ticker:
            def __init__(self, *args, **kwargs):
                self.info = {"currentPrice": 10.0, "previousClose": 9.0}

        with patch.object(market_data.yf, "Ticker", Ticker):
            assert market_data.get_quote("X")[2] == Decimal("9.0")

    def test_a_missing_close_is_none_not_zero(self):
        class Ticker:
            def __init__(self, *args, **kwargs):
                self.info = {"currentPrice": 10.0}

        with patch.object(market_data.yf, "Ticker", Ticker):
            assert market_data.get_quote("X")[2] is None

    def test_refreshing_stores_it(self, seeded):
        hold(seeded, "AAPL", "1", "100")
        with patch.object(market_data, "get_quote",
                          return_value=(Decimal("130"), "Apple Inc.", Decimal("125"))):
            operations.refresh_prices(seeded)

        with cursor() as cur:
            cur.execute("SELECT current_price, previous_close FROM stocks WHERE symbol = 'AAPL'")
            assert cur.fetchone() == (Decimal("130"), Decimal("125"))

    def test_a_refresh_without_one_leaves_the_stored_value_alone(self, seeded):
        """Blanking it would erase a figure the page is already showing."""
        hold(seeded, "AAPL", "1", "100")
        with patch.object(market_data, "get_quote",
                          return_value=(Decimal("130"), "Apple Inc.", None)):
            operations.refresh_prices(seeded)

        with cursor() as cur:
            cur.execute("SELECT previous_close FROM stocks WHERE symbol = 'AAPL'")
            assert cur.fetchone()[0] == Decimal("90")

    def test_it_is_rejected_if_it_is_nonsense(self, seeded):
        with pytest.raises(Exception):
            with cursor(commit=True) as cur:
                cur.execute("UPDATE stocks SET previous_close = 'NaN' WHERE symbol = 'AAPL'")


class TestPerPosition:
    def test_a_rise_since_the_close(self, seeded):
        hold(seeded, "AAPL", "10", "100")          # close 90, now 100
        row = next(r for r in operations.account_summary(seeded).rows if r["symbol"] == "AAPL")
        assert row["todays_change"] == Decimal("100")
        assert round(row["todays_percent"], 4) == round(Decimal("100") / Decimal("9"), 4)

    def test_a_fall_since_the_close(self, seeded):
        hold(seeded, "MSFT", "5", "200")           # close 220, now 200
        row = next(r for r in operations.account_summary(seeded).rows if r["symbol"] == "MSFT")
        assert row["todays_change"] == Decimal("-100")

    def test_no_close_yet_is_none_not_zero(self, seeded):
        """A zero would claim the price is unchanged today, which is a
        different statement from not knowing."""
        stocks.upsert_stock("NEW", "New Co", Decimal("50"))
        hold(seeded, "NEW", "2", "50")
        row = next(r for r in operations.account_summary(seeded).rows if r["symbol"] == "NEW")
        assert row["todays_change"] is None
        assert row["todays_percent"] is None

    def test_an_unpriced_holding_has_no_change(self, seeded):
        hold(seeded, "AAPL", "1", "100")
        with cursor(commit=True) as cur:
            cur.execute("UPDATE stocks SET current_price = NULL WHERE symbol = 'AAPL'")
        row = operations.account_summary(seeded).rows[0]
        assert row["todays_change"] is None

    def test_it_scales_with_the_position(self, seeded):
        hold(seeded, "AAPL", "100", "100")
        row = operations.account_summary(seeded).rows[0]
        assert row["todays_change"] == Decimal("1000")   # (100-90) * 100


class TestForTheWholeAccount:
    def test_it_sums_the_positions(self, seeded):
        hold(seeded, "AAPL", "10", "100")          # +100
        hold(seeded, "MSFT", "5", "200")           # -100
        assert operations.account_summary(seeded).todays_change == 0

    def test_the_percentage_is_against_the_previous_value(self, seeded):
        hold(seeded, "AAPL", "10", "100")          # close 90 * 10 = 900, now 1000
        summary = operations.account_summary(seeded)
        assert summary.todays_change == Decimal("100")
        assert round(summary.todays_percent, 4) == round(Decimal("1000") / Decimal("90"), 4)

    def test_an_empty_account_reports_nothing_rather_than_zero(self, seeded):
        summary = operations.account_summary(seeded)
        assert summary.todays_change is None
        assert summary.todays_percent is None

    def test_positions_with_no_close_are_left_out_of_the_total(self, seeded):
        stocks.upsert_stock("NEW", "New Co", Decimal("50"))
        hold(seeded, "AAPL", "10", "100")          # +100, has a close
        hold(seeded, "NEW", "10", "50")            # no close
        assert operations.account_summary(seeded).todays_change == Decimal("100")

    def test_cash_is_not_part_of_it(self, seeded):
        """Cash does not move with the market; counting it would dilute the
        percentage toward zero."""
        hold(seeded, "AAPL", "10", "100")
        summary = operations.account_summary(seeded)
        assert summary.todays_percent > 10   # tiny basis, not the whole account


class TestThePage:
    def test_the_portfolio_shows_todays_change(self, client, seeded):
        hold(seeded, "AAPL", "10", "100")
        page = client.get("/").data
        assert b"Today" in page
        assert b"Against the previous close" in page

    def test_it_says_so_when_no_close_is_known(self, client, seeded):
        stocks.upsert_stock("NEW", "New Co", Decimal("50"))
        hold(seeded, "NEW", "2", "50")
        page = client.get("/").data
        assert b"Not known yet" in page
        assert b"Refresh prices to record a previous close" in page

    def test_a_position_without_a_close_shows_a_dash(self, client, seeded):
        stocks.upsert_stock("NEW", "New Co", Decimal("50"))
        hold(seeded, "NEW", "2", "50")
        assert b"&mdash;" in client.get("/").data or b"\xe2\x80\x94" in client.get("/").data

    def test_the_glossary_explains_it(self, client, seeded):
        hold(seeded, "AAPL", "10", "100")
        assert b"since the previous session" in client.get("/").data


class TestAZeroCloseIsNotAPrice:
    """previous_close is divided by and summed into a basis, so zero is not
    a usable value — NULL is how "not known" is spelled."""

    def test_the_database_refuses_zero(self, seeded):
        with pytest.raises(Exception):
            with cursor(commit=True) as cur:
                cur.execute("UPDATE stocks SET previous_close = 0 WHERE symbol = 'AAPL'")

    def test_a_positive_close_is_still_accepted(self, seeded):
        with cursor(commit=True) as cur:
            cur.execute("UPDATE stocks SET previous_close = 0.01 WHERE symbol = 'AAPL'")
        with cursor() as cur:
            cur.execute("SELECT previous_close FROM stocks WHERE symbol = 'AAPL'")
            assert cur.fetchone()[0] == Decimal("0.01")

    def test_null_is_still_accepted(self, seeded):
        with cursor(commit=True) as cur:
            cur.execute("UPDATE stocks SET previous_close = NULL WHERE symbol = 'AAPL'")
        assert operations.account_summary(seeded).todays_change is None

    def test_current_price_may_still_be_zero(self, seeded):
        """It is only ever displayed, never divided by."""
        with cursor(commit=True) as cur:
            cur.execute("UPDATE stocks SET current_price = 0 WHERE symbol = 'AAPL'")
        with cursor() as cur:
            cur.execute("SELECT current_price FROM stocks WHERE symbol = 'AAPL'")
            assert cur.fetchone()[0] == 0

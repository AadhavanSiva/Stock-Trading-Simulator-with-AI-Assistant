"""Following tickers without owning them."""
from decimal import Decimal
from unittest.mock import patch

import pytest

from portfolio_tracker import operations
from portfolio_tracker.db import cursor
from portfolio_tracker.models import portfolio, stocks, watchlist
from portfolio_tracker.services import market_data


@pytest.fixture
def seeded(user):
    stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"), previous_close=Decimal("90"))
    stocks.upsert_stock("MSFT", "Microsoft Corp.", Decimal("200"), previous_close=Decimal("220"))
    return user


def quoting(price="100", name="Apple Inc.", previous_close="90"):
    return patch.object(
        market_data, "get_quote",
        return_value=(Decimal(price), name,
                      Decimal(previous_close) if previous_close else None),
    )


class TestFollowing:
    def test_following_adds_it(self, seeded):
        with quoting():
            symbol, added = operations.watch(seeded, "aapl")
        assert (symbol, added) == ("AAPL", True)
        assert watchlist.symbols(seeded) == ["AAPL"]

    def test_following_twice_is_idempotent(self, seeded):
        with quoting():
            operations.watch(seeded, "AAPL")
            _, added = operations.watch(seeded, "AAPL")
        assert added is False
        assert watchlist.count(seeded) == 1

    def test_the_symbol_is_normalised(self, seeded):
        with quoting():
            operations.watch(seeded, "  aapl  ")
        assert watchlist.contains(seeded, "AAPL")

    def test_an_unknown_ticker_is_refused_before_it_is_stored(self, seeded):
        """A typo must not reach the list."""
        with patch.object(market_data, "get_quote", return_value=(None, "ZZZZ", None)):
            with pytest.raises(operations.ValidationError):
                operations.watch(seeded, "ZZZZ")
        assert watchlist.count(seeded) == 0

    def test_following_registers_an_unseen_company(self, seeded):
        """A watchlist is often the first place a ticker is mentioned, and
        the foreign key needs a row in `stocks`."""
        with quoting(price="42", name="New Co", previous_close="40"):
            operations.watch(seeded, "NEW")
        with cursor() as cur:
            cur.execute("SELECT company_name FROM stocks WHERE symbol = 'NEW'")
            assert cur.fetchone()[0] == "New Co"

    def test_unfollowing_removes_it(self, seeded):
        with quoting():
            operations.watch(seeded, "AAPL")
        assert operations.unwatch(seeded, "AAPL") is True
        assert watchlist.symbols(seeded) == []

    def test_unfollowing_something_not_followed_is_harmless(self, seeded):
        assert operations.unwatch(seeded, "AAPL") is False

    def test_watchlists_are_scoped_to_their_account(self, seeded, other_user):
        with quoting():
            operations.watch(seeded, "AAPL")
        assert watchlist.symbols(other_user) == []

    def test_two_accounts_can_follow_the_same_ticker(self, seeded, other_user):
        with quoting():
            operations.watch(seeded, "AAPL")
            operations.watch(other_user, "AAPL")
        assert watchlist.symbols(other_user) == ["AAPL"]


class TestTheRows:
    def test_it_reports_price_and_todays_change(self, seeded):
        with quoting():
            operations.watch(seeded, "AAPL")
        row, = operations.watchlist_rows(seeded)
        assert row.price == Decimal("100")
        assert row.todays_change == Decimal("10")

    def test_a_fall_is_negative(self, seeded):
        with quoting(price="200", name="Microsoft Corp.", previous_close="220"):
            operations.watch(seeded, "MSFT")
        row, = operations.watchlist_rows(seeded)
        assert row.todays_change == Decimal("-20")

    def test_no_close_yet_is_none_not_zero(self, seeded):
        with quoting(price="50", name="New Co", previous_close=None):
            operations.watch(seeded, "NEW")
        row, = operations.watchlist_rows(seeded)
        assert row.todays_change is None and row.todays_percent is None

    def test_an_unpriced_ticker_still_appears(self, seeded):
        with quoting():
            operations.watch(seeded, "AAPL")
        with cursor(commit=True) as cur:
            cur.execute("UPDATE stocks SET current_price = NULL WHERE symbol = 'AAPL'")
        row, = operations.watchlist_rows(seeded)
        assert row.price is None and row.symbol == "AAPL"

    def test_a_held_ticker_is_marked(self, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("1"))
        with quoting():
            operations.watch(seeded, "AAPL")
        assert operations.watchlist_rows(seeded)[0].owned is True

    def test_an_unheld_ticker_is_not(self, seeded):
        with quoting():
            operations.watch(seeded, "AAPL")
        assert operations.watchlist_rows(seeded)[0].owned is False


class TestRefreshing:
    def test_it_requotes_followed_tickers(self, seeded):
        with quoting():
            operations.watch(seeded, "AAPL")
        with quoting(price="130", previous_close="125"):
            report = operations.refresh_watchlist(seeded)
        assert report.updated == 1
        assert operations.watchlist_rows(seeded)[0].price == Decimal("130")

    def test_it_does_not_value_the_account(self, seeded):
        """A watched ticker is not part of the account's worth, so quoting
        one must not move the performance chart or the leaderboard."""
        from portfolio_tracker.models import value_history

        with quoting():
            operations.watch(seeded, "AAPL")
        before = value_history.count(seeded)
        with quoting(price="130", previous_close="125"):
            operations.refresh_watchlist(seeded)
        assert value_history.count(seeded) == before

    def test_one_failure_does_not_stop_the_rest(self, seeded):
        from portfolio_tracker.errors import MarketDataUnavailable

        with quoting():
            operations.watch(seeded, "AAPL")
        with quoting(price="200", name="Microsoft Corp.", previous_close="220"):
            operations.watch(seeded, "MSFT")

        def quote(symbol, fresh=False):
            if symbol == "AAPL":
                raise MarketDataUnavailable(symbol)
            return Decimal("210"), symbol, Decimal("205")

        with patch.object(market_data, "get_quote", side_effect=quote):
            report = operations.refresh_watchlist(seeded)
        assert report.updated == 1 and report.failed == 1

    def test_an_empty_watchlist_refreshes_to_nothing(self, seeded):
        assert operations.refresh_watchlist(seeded).total == 0


class TestThePages:
    def test_the_page_needs_a_signed_in_account(self, db):
        import web as web_module

        web_module.app.config.update(TESTING=True)
        with web_module.app.test_client() as anonymous:
            assert anonymous.get("/watchlist").status_code == 302

    def test_an_empty_watchlist_explains_itself(self, client, seeded):
        assert b"Nothing followed yet" in client.get("/watchlist").data

    def test_it_lists_what_you_follow(self, client, seeded):
        with quoting():
            operations.watch(seeded, "AAPL")
        page = client.get("/watchlist").data
        assert b"AAPL" in page and b"Apple Inc." in page

    def test_adding_from_the_form(self, client, seeded):
        with quoting():
            client.post("/watchlist/add", data={"symbol": "AAPL"})
        assert watchlist.contains(seeded, "AAPL")

    def test_removing_from_the_form(self, client, seeded):
        with quoting():
            operations.watch(seeded, "AAPL")
        client.post("/watchlist/remove", data={"symbol": "AAPL"})
        assert not watchlist.contains(seeded, "AAPL")

    def test_the_stock_page_offers_follow(self, client, seeded):
        with quoting():
            assert b"Follow AAPL" in client.get("/stock/AAPL").data

    def test_the_stock_page_offers_unfollow_once_following(self, client, seeded):
        with quoting():
            operations.watch(seeded, "AAPL")
            assert b"Unfollow AAPL" in client.get("/stock/AAPL").data

    def test_adding_needs_a_csrf_token(self, db):
        import web as web_module

        web_module.app.config.update(TESTING=True)
        with web_module.app.test_client() as anonymous:
            assert anonymous.post("/watchlist/add",
                                  data={"symbol": "AAPL"}).status_code == 400

    def test_a_bad_next_is_not_followed_off_site(self, client, seeded):
        """safe_next guards these redirects too."""
        with quoting():
            response = client.post("/watchlist/add",
                                   data={"symbol": "AAPL",
                                         "next": "https://evil.example/x"})
        assert "evil.example" not in response.headers.get("Location", "")


class TestDeletion:
    def test_deleting_an_account_removes_its_watchlist(self, seeded):
        with quoting():
            operations.watch(seeded, "AAPL")
        operations.delete_account(seeded, "DELETE")
        assert watchlist.count(seeded) == 0

"""Prices refresh themselves, without anyone waiting for it.

The behaviour under test is mostly about what *doesn't* happen: no
refresh when prices are fresh, none when the market is shut, not ten when
ten people look at once, and above all no page waiting on a network call.
"""
import threading
import time
from decimal import Decimal
from unittest.mock import patch

import pytest

from portfolio_tracker import operations
from portfolio_tracker.db import cursor
from portfolio_tracker.models import portfolio, stocks, value_history
from portfolio_tracker.services import market_data


@pytest.fixture(autouse=True)
def no_claims_leak():
    """A claimed symbol left behind would mute refreshes in later tests."""
    operations._refreshing.clear()
    yield
    operations._refreshing.clear()


@pytest.fixture
def open_market():
    with patch.object(market_data, "market_is_open", return_value=True):
        yield


@pytest.fixture
def held(user):
    stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"),
                        previous_close=Decimal("99"))
    portfolio.record_purchase(user, "AAPL", "Apple Inc.",
                              Decimal("100"), Decimal("2"))
    return user


def age(symbol, seconds):
    """Backdate a price so it reads as `seconds` old."""
    with cursor(commit=True) as cur:
        cur.execute(
            "UPDATE stocks SET updated_at = now() - make_interval(secs => %s) "
            "WHERE symbol = %s", (seconds, symbol))


def quoting(price="150"):
    return patch.object(market_data, "get_quote",
                        return_value=(Decimal(price), "Apple Inc.", Decimal("99")))


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestWhenItRefreshes:
    def test_a_stale_price_triggers_exactly_one_refresh(self, held, open_market):
        age("AAPL", 600)
        with quoting() as get_quote:
            assert operations.ensure_prices_fresh(held, ["AAPL"]) == ["AAPL"]
            assert wait_for(lambda: get_quote.call_count >= 1)
            time.sleep(0.15)
            assert get_quote.call_count == 1

    def test_a_fresh_price_triggers_none(self, held, open_market):
        age("AAPL", 10)
        with quoting() as get_quote:
            assert operations.ensure_prices_fresh(held, ["AAPL"]) == []
            time.sleep(0.1)
            assert get_quote.call_count == 0

    def test_a_price_with_no_timestamp_counts_as_stale(self, held, open_market):
        """NULL means nobody recorded when it was fetched, which is not the
        same as it being current."""
        with cursor(commit=True) as cur:
            cur.execute("UPDATE stocks SET updated_at = NULL WHERE symbol = 'AAPL'")
        with quoting():
            assert operations.ensure_prices_fresh(held, ["AAPL"]) == ["AAPL"]

    def test_the_boundary_is_five_minutes(self, held, open_market):
        assert operations.STALE_AFTER_SECONDS == 300
        age("AAPL", 290)
        with quoting():
            assert operations.ensure_prices_fresh(held, ["AAPL"]) == []
        age("AAPL", 310)
        with quoting():
            assert operations.ensure_prices_fresh(held, ["AAPL"]) == ["AAPL"]

    def test_no_symbols_does_nothing(self, held, open_market):
        assert operations.ensure_prices_fresh(held, []) == []


class TestTheMarketClock:
    def test_nothing_refreshes_while_the_market_is_closed(self, held):
        age("AAPL", 600)
        with patch.object(market_data, "market_is_open", return_value=False), \
                quoting() as get_quote:
            assert operations.ensure_prices_fresh(held, ["AAPL"]) == []
            time.sleep(0.1)
            assert get_quote.call_count == 0

    def test_the_clock_is_not_consulted_when_prices_are_fresh(self, held):
        """A closed market should cost nothing on a page that is current."""
        age("AAPL", 10)
        with patch.object(market_data, "market_is_open") as clock:
            operations.ensure_prices_fresh(held, ["AAPL"])
            assert clock.call_count == 0

    def test_an_unreachable_clock_holds_off(self):
        """Conservative direction: unknown means do not quote into the
        night on a guess. The manual refresh is unaffected."""
        from portfolio_tracker.errors import MarketDataUnavailable

        market_data.clear_quote_cache()
        with patch.object(market_data, "_request",
                          side_effect=MarketDataUnavailable("clock")):
            assert market_data._real_market_is_open() is False

    def test_the_clock_answer_is_cached(self):
        market_data.clear_quote_cache()
        with patch.object(market_data, "_request",
                          return_value={"is_open": True}) as request:
            assert market_data._real_market_is_open() is True
            assert market_data._real_market_is_open() is True
            assert request.call_count == 1


class TestConcurrency:
    def test_ten_viewers_cause_one_refresh(self, held, open_market):
        age("AAPL", 600)
        started = []
        release = threading.Event()

        def slow_quote(symbol, fresh=False):
            started.append(symbol)
            release.wait(3)
            return Decimal("150"), "Apple Inc.", Decimal("99")

        with patch.object(market_data, "get_quote", side_effect=slow_quote):
            claimed = [operations.ensure_prices_fresh(held, ["AAPL"])
                       for _ in range(10)]
            assert wait_for(lambda: started)
            release.set()
            time.sleep(0.2)

        assert sum(1 for c in claimed if c) == 1, claimed
        assert len(started) == 1

    def test_simultaneous_requests_do_not_duplicate(self, held, open_market):
        age("AAPL", 600)
        outcomes = []
        release = threading.Event()

        def slow_quote(symbol, fresh=False):
            release.wait(3)
            return Decimal("150"), "Apple Inc.", Decimal("99")

        def viewer():
            outcomes.append(operations.ensure_prices_fresh(held, ["AAPL"]))

        with patch.object(market_data, "get_quote", side_effect=slow_quote):
            threads = [threading.Thread(target=viewer) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
                assert not t.is_alive()
            release.set()
            time.sleep(0.2)

        assert sum(1 for o in outcomes if o) == 1, outcomes

    def test_the_claim_is_released_afterwards(self, held, open_market):
        age("AAPL", 600)
        with quoting():
            operations.ensure_prices_fresh(held, ["AAPL"])
            assert wait_for(lambda: "AAPL" not in operations._refreshing)

    def test_a_failing_refresh_still_releases_its_claim(self, held, open_market):
        """Otherwise one outage would mute that symbol until a restart."""
        age("AAPL", 600)
        with patch.object(market_data, "get_quote",
                          side_effect=RuntimeError("boom")):
            operations.ensure_prices_fresh(held, ["AAPL"])
            assert wait_for(lambda: "AAPL" not in operations._refreshing)


class TestItNeverBlocks:
    def test_ensure_returns_before_the_quote_finishes(self, held, open_market):
        age("AAPL", 600)
        release = threading.Event()

        def slow_quote(symbol, fresh=False):
            release.wait(5)
            return Decimal("150"), "Apple Inc.", Decimal("99")

        with patch.object(market_data, "get_quote", side_effect=slow_quote):
            start = time.monotonic()
            operations.ensure_prices_fresh(held, ["AAPL"])
            elapsed = time.monotonic() - start
            release.set()

        assert elapsed < 0.5, f"ensure_prices_fresh waited {elapsed:.2f}s"

    def test_the_portfolio_page_renders_without_waiting(self, client, held, open_market):
        age("AAPL", 600)
        release = threading.Event()

        def slow_quote(symbol, fresh=False):
            release.wait(5)
            return Decimal("150"), "Apple Inc.", Decimal("99")

        with patch.object(market_data, "get_quote", side_effect=slow_quote):
            start = time.monotonic()
            response = client.get("/")
            elapsed = time.monotonic() - start
            release.set()
            time.sleep(0.1)

        assert response.status_code == 200
        assert elapsed < 1.0, f"the page waited {elapsed:.2f}s on the network"

    def test_a_page_renders_even_if_the_refresh_cannot_start(self, client, held):
        with patch.object(operations, "ensure_prices_fresh",
                          side_effect=RuntimeError("pool is gone")):
            assert client.get("/").status_code == 200


class TestWhatItWrites:
    def test_the_new_price_is_stored(self, held, open_market):
        age("AAPL", 600)
        with quoting("175"):
            operations.ensure_prices_fresh(held, ["AAPL"])
            assert wait_for(
                lambda: stocks.prices_as_of(["AAPL"]) is not None
                and operations.account_summary(held).rows[0]["current_price"]
                == Decimal("175"))

    def test_it_records_a_value_sample(self, held, open_market):
        """So the performance chart and the leaderboard fill in on their
        own rather than waiting for someone to click a button twice."""
        before = value_history.count(held)
        age("AAPL", 600)
        with quoting():
            operations.ensure_prices_fresh(held, ["AAPL"])
            assert wait_for(lambda: value_history.count(held) > before)

    def test_one_bad_symbol_does_not_stop_the_others(self, held, open_market):
        stocks.upsert_stock("MSFT", "Microsoft", Decimal("400"))
        age("AAPL", 600)
        age("MSFT", 600)

        def quote(symbol, fresh=False):
            if symbol == "AAPL":
                raise RuntimeError("no")
            return Decimal("410"), "Microsoft", Decimal("405")

        with patch.object(market_data, "get_quote", side_effect=quote):
            operations.ensure_prices_fresh(held, ["AAPL", "MSFT"])
            assert wait_for(
                lambda: stocks.prices_as_of(["MSFT"]) is not None
                and not operations._refreshing)


class TestTheAsOfStamp:
    def test_it_reports_the_oldest_of_several(self, held):
        """A page is only as current as its stalest holding."""
        stocks.upsert_stock("MSFT", "Microsoft", Decimal("400"))
        age("AAPL", 60)
        age("MSFT", 6000)
        shown = stocks.prices_as_of(["AAPL", "MSFT"])
        newest = stocks.prices_as_of(["AAPL"])
        assert shown < newest

    def test_it_is_none_when_nothing_is_known(self, db):
        assert stocks.prices_as_of([]) is None

    @pytest.mark.parametrize("path", ["/", "/balance", "/watchlist"])
    def test_the_pages_show_it(self, client, held, path):
        from portfolio_tracker.models import watchlist as watchlist_model

        watchlist_model.add(held, "AAPL")
        body = client.get(path).data
        assert b"Prices as of" in body or b"no recorded time" in body

    def test_it_says_prices_are_delayed(self, client, held):
        assert b"delayed by the data provider" in client.get("/").data

    def test_the_manual_refresh_is_still_offered(self, client, held):
        assert b"Refresh now" in client.get("/").data


class TestTheOneWorkerCeilingIsDocumented:
    def test_the_claim_set_carries_the_warning(self):
        """Whoever adds a second worker should meet this before the bug."""
        import inspect

        source = inspect.getsource(operations)
        block = source[source.index("_refreshing = set()") - 2000:
                       source.index("_refreshing = set()")]
        assert "PER PROCESS" in block
        assert "SKIP LOCKED" in block
        assert "stocks.updated_at" in block

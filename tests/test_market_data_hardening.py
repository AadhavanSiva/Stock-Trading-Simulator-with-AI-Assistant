"""yfinance hardening: time limits, outages surfacing as errors, and the
short-lived quote cache. yfinance itself is always faked; nothing here
touches the network."""
import threading
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest
from yfinance.exceptions import YFPricesMissingError

from portfolio_tracker import config, operations
from portfolio_tracker.errors import MarketDataUnavailable, ValidationError
from portfolio_tracker.models import history, portfolio, stocks
from portfolio_tracker.services import market_data


class RecordingTicker:
    """Counts every Ticker created and records history() arguments."""

    def __init__(self, info=None, frame=None, error=None, delay=0.0):
        self.created = 0
        self.history_calls = []
        self._info, self._frame, self._error, self._delay = info, frame, error, delay

    def __call__(self, symbol):
        self.created += 1
        return self

    @property
    def info(self):
        if self._delay:
            time.sleep(self._delay)
        if self._error:
            raise self._error
        return self._info

    def history(self, **kwargs):
        self.history_calls.append(kwargs)
        if self._delay:
            time.sleep(self._delay)
        if self._error:
            raise self._error
        return self._frame if self._frame is not None else pd.DataFrame()


def using(ticker):
    return patch.object(market_data.yf, "Ticker", ticker)


def bars():
    index = pd.date_range("2026-01-02", periods=2, freq="D")
    return pd.DataFrame({"Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0],
                         "Close": [1.0, 2.0], "Volume": [10, 20]}, index=index)


class TestTheSuiteStaysOffline:
    def test_an_unmocked_lookup_fails_loudly_instead_of_calling_yahoo(self):
        """conftest replaces yfinance.Ticker; market_data must not swallow it."""
        with pytest.raises(BaseException) as caught:
            market_data.get_quote("AAPL")
        assert type(caught.value).__name__ == "RealMarketDataCall"


class TestTimeLimits:
    def test_history_passes_an_explicit_timeout_and_raise_errors(self):
        ticker = RecordingTicker(frame=bars())
        with using(ticker):
            market_data.get_price_history("AAPL", period="1y")
            market_data.get_intraday("AAPL", period="1d", interval="5m")
        for call in ticker.history_calls:
            assert call["timeout"] == config.MARKET_HISTORY_TIMEOUT
            assert call["raise_errors"] is True
        assert ticker.history_calls[0]["period"] == "1y"
        assert ticker.history_calls[1]["interval"] == "5m"

    def test_a_hung_quote_gives_up_at_the_deadline(self, monkeypatch, caplog):
        """Ticker.info accepts no timeout, so the deadline is enforced around it."""
        monkeypatch.setattr(config, "MARKET_QUOTE_TIMEOUT", 0.2)
        started = time.monotonic()
        with using(RecordingTicker(info={"currentPrice": 1.0}, delay=3)):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("SLOW")
        assert time.monotonic() - started < 2
        assert "took longer than 0.2s" in caplog.text

    def test_a_hung_history_download_gives_up_at_the_deadline(self, monkeypatch):
        monkeypatch.setattr(config, "MARKET_HISTORY_TIMEOUT", 0.2)
        started = time.monotonic()
        with using(RecordingTicker(frame=bars(), delay=3)):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_price_history("SLOW")
        assert time.monotonic() - started < 2


class TestOutagesAreErrors:
    def test_a_failed_history_download_raises_instead_of_returning_empty(self, caplog):
        with using(RecordingTicker(error=RuntimeError("curl: (28) Operation timed out"))):
            with pytest.raises(MarketDataUnavailable) as caught:
                market_data.get_price_history("AAPL")
        assert "timed out" not in str(caught.value)
        assert "curl: (28) Operation timed out" in caplog.text

    def test_the_loader_no_longer_reports_zero_new_days_in_an_outage(self, db):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        with using(RecordingTicker(error=RuntimeError("Yahoo is down"))):
            with pytest.raises(MarketDataUnavailable):
                history.load_history_for_symbol("AAPL")

    def test_yahoo_saying_there_are_no_prices_is_still_empty_not_an_error(self):
        missing = YFPricesMissingError("AAPL", "(period=1d)")
        with using(RecordingTicker(error=missing)):
            assert market_data.get_intraday("AAPL").empty

    def test_an_unknown_symbol_is_empty_not_an_outage(self):
        not_found = RuntimeError("HTTP Error 404")
        not_found.response = SimpleNamespace(status_code=404)
        with using(RecordingTicker(error=not_found)):
            assert market_data.get_price_history("ZZZZ").empty

    def test_a_server_error_is_an_outage(self):
        server = RuntimeError("HTTP Error 503")
        server.response = SimpleNamespace(status_code=503)
        with using(RecordingTicker(error=server)):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_price_history("AAPL")

    def test_a_failed_quote_raises_a_message_safe_to_show(self):
        with using(RecordingTicker(error=ConnectionError("[Errno 11001] getaddrinfo failed"))):
            with pytest.raises(MarketDataUnavailable) as caught:
                market_data.get_quote("AAPL")
        assert str(caught.value) == ("Could not look up AAPL right now. Yahoo Finance "
                                     "didn't respond, so try again in a minute.")

    def test_lookup_turns_an_outage_into_a_validation_error(self, user):
        with using(RecordingTicker(error=ConnectionError("getaddrinfo failed"))):
            with pytest.raises(ValidationError) as caught:
                operations.look_up(user, "AAPL")
        assert "getaddrinfo" not in str(caught.value)

    def test_one_outage_does_not_stop_a_bulk_refresh(self, user):
        for symbol in ("AAPL", "MSFT"):
            stocks.upsert_stock(symbol, symbol, Decimal("100"))
            portfolio.record_purchase(user, symbol, symbol, Decimal("100"), Decimal("1"))

        def price(symbol, fresh=False):
            if symbol == "AAPL":
                raise MarketDataUnavailable(symbol)
            return Decimal("120")

        with patch.object(market_data, "get_live_price", side_effect=price):
            report = operations.refresh_prices(user)
        assert report.updated == 1 and report.failed == 1
        failed = [o for o in report.outcomes if not o.ok][0]
        assert failed.message == "Yahoo Finance didn't respond; try again in a minute"


class TestQuoteCache:
    def test_a_repeat_lookup_within_the_window_does_not_ask_yahoo_again(self):
        ticker = RecordingTicker(info={"currentPrice": 10.0, "longName": "Ten"})
        with using(ticker):
            first = market_data.get_quote("TEN")
            second = market_data.get_quote("ten ")
        assert first == second == (Decimal("10.0"), "Ten")
        assert ticker.created == 1

    def test_fresh_bypasses_the_cache_and_updates_it(self):
        ticker = RecordingTicker(info={"currentPrice": 10.0})
        with using(ticker):
            market_data.get_quote("TEN")
            ticker._info = {"currentPrice": 11.0}
            assert market_data.get_quote("TEN", fresh=True)[0] == Decimal("11.0")
            assert market_data.get_quote("TEN")[0] == Decimal("11.0")
        assert ticker.created == 2

    def test_entries_expire(self, monkeypatch):
        monkeypatch.setattr(config, "QUOTE_CACHE_SECONDS", 0.05)
        ticker = RecordingTicker(info={"currentPrice": 10.0})
        with using(ticker):
            market_data.get_quote("TEN")
            time.sleep(0.1)
            market_data.get_quote("TEN")
        assert ticker.created == 2

    def test_a_zero_window_disables_caching(self, monkeypatch):
        monkeypatch.setattr(config, "QUOTE_CACHE_SECONDS", 0)
        ticker = RecordingTicker(info={"currentPrice": 10.0})
        with using(ticker):
            market_data.get_quote("TEN")
            market_data.get_quote("TEN")
        assert ticker.created == 2

    def test_failures_are_not_cached(self):
        ticker = RecordingTicker(error=ConnectionError("down"))
        with using(ticker):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("TEN")
            ticker._error = None
            ticker._info = {"currentPrice": 10.0}
            assert market_data.get_quote("TEN")[0] == Decimal("10.0")

    def test_the_cache_is_bounded(self, monkeypatch):
        monkeypatch.setattr(market_data, "_QUOTE_CACHE_LIMIT", 5)
        with using(RecordingTicker(info={"currentPrice": 1.0})):
            for i in range(20):
                market_data.get_quote(f"S{i}")
        assert len(market_data._quotes) == 5

    def test_concurrent_lookups_are_safe(self):
        ticker = RecordingTicker(info={"currentPrice": 1.0})
        errors = []

        def worker(n):
            try:
                for i in range(30):
                    market_data.get_quote(f"T{(n + i) % 7}")
            except Exception as exc:     # pragma: no cover - reported below
                errors.append(exc)

        with using(ticker):
            threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        assert errors == []


class TestWhoGetsAFreshPrice:
    """Pages may reuse a quote for a few seconds; trades never do."""

    def spy(self):
        return patch.object(market_data, "get_quote", return_value=(Decimal("100"), "Test Co"))

    def test_a_page_lookup_may_use_the_cache(self, user):
        with self.spy() as get_quote:
            operations.look_up(user, "TST")
        assert get_quote.call_args.kwargs.get("fresh") is False

    def test_buying_always_fetches_a_fresh_price(self, user):
        with self.spy() as get_quote:
            operations.buy(user, "TST", "1")
        assert get_quote.call_args.kwargs.get("fresh") is True

    def test_selling_always_fetches_a_fresh_price(self, user):
        stocks.upsert_stock("TST", "Test Co", Decimal("100"))
        portfolio.record_purchase(user, "TST", "Test Co", Decimal("100"), Decimal("2"))
        with patch.object(market_data, "get_live_price", return_value=Decimal("100")) as live:
            operations.sell(user, "TST", "1")
        assert live.call_args.kwargs.get("fresh") is True

    def test_refreshing_always_fetches_fresh_prices(self, user):
        stocks.upsert_stock("TST", "Test Co", Decimal("100"))
        portfolio.record_purchase(user, "TST", "Test Co", Decimal("100"), Decimal("2"))
        with patch.object(market_data, "get_live_price", return_value=Decimal("100")) as live:
            operations.refresh_prices(user)
        assert live.call_args.kwargs.get("fresh") is True

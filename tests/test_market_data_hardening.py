"""Alpaca's failure modes, taken from its documented error table.

The status codes here are the ones Alpaca actually specifies, not the
ones that seem natural. That distinction has already cost this project
twice: Gemini answers 400 for a rejected key where 401 would be expected,
and yfinance returned an empty frame for an outage where an exception
would be. So each code below is asserted rather than assumed:

    400  invalid parameter
    401  missing or invalid credentials
    403  forbidden — in practice, the data plan
    429  rate limit
    500  their side

Plus the one that is not an error at all and is the most dangerous:
a 200 whose body is `{"bars": null}`.
"""
import threading
import time
from decimal import Decimal
from unittest.mock import patch

import pytest
from conftest import (
    FakeResponse, RealMarketDataCall, asset_payload, bars_payload,
    route_alpaca, snapshot_payload,
)

from portfolio_tracker import config, operations
from portfolio_tracker.errors import (
    MarketDataCredentialsRejected, MarketDataUnavailable, UnknownSymbol,
)
from portfolio_tracker.models import portfolio, stocks
from portfolio_tracker.services import market_data


def responding(status, payload=None, text="", headers=None):
    """Every Alpaca endpoint answering with one status."""
    return patch.object(
        market_data._session, "get",
        return_value=FakeResponse(status, payload, text=text, headers=headers),
    )


class TestNothingReachesTheNetwork:
    def test_an_unmocked_lookup_fails_loudly(self):
        """The guard is on the session, so it catches any endpoint —
        including one added later that nobody remembers to stub."""
        with pytest.raises(RealMarketDataCall):
            market_data.get_quote("AAPL")

    def test_it_also_catches_history(self):
        with pytest.raises(RealMarketDataCall):
            market_data.get_price_history("AAPL")

    def test_it_also_catches_the_catalogue(self):
        with pytest.raises(RealMarketDataCall):
            market_data.resolve_symbol("AAPL")


class TestCredentialsAreRejectedWith401:
    """Alpaca's table says 401 for a missing or invalid key. It is worth
    pinning, because the Gemini client in this same project answers 400
    for the same mistake."""

    def test_a_bad_key_raises(self, caplog):
        with responding(401, text='{"message":"access key verification failed"}'):
            with pytest.raises(MarketDataCredentialsRejected):
                market_data.get_quote("AAPL")

    def test_it_is_still_caught_as_unavailable(self):
        """Existing callers only know MarketDataUnavailable."""
        with responding(401, text="nope"):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("AAPL")

    def test_the_log_names_the_setting_to_fix(self, caplog):
        """A misconfiguration will not fix itself on a retry, so the log
        has to say which value is wrong."""
        with caplog.at_level("ERROR"), responding(401, text="nope"):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("AAPL")
        assert "ALPACA_API_KEY_ID" in caplog.text

    def test_the_reader_is_not_shown_the_internals(self):
        with responding(401, text='{"message":"access key verification failed"}'):
            try:
                market_data.get_quote("AAPL")
            except MarketDataUnavailable as exc:
                message = str(exc)
        assert "access key" not in message
        assert "401" not in message

    def test_missing_keys_are_refused_before_any_request(self, monkeypatch):
        """With no key configured, fail with instructions rather than let
        Alpaca answer a puzzling 401."""
        monkeypatch.setattr(config, "ALPACA_API_KEY_ID", None)
        monkeypatch.setattr(config, "ALPACA_API_SECRET_KEY", None)
        with pytest.raises(config.ConfigurationError, match="ALPACA_API_KEY_ID"):
            market_data.get_quote("AAPL")


class TestThePlanIsRefusedWith403:
    def test_a_forbidden_feed_raises(self):
        with responding(403, text='{"message":"subscription does not permit"}'):
            with pytest.raises(MarketDataCredentialsRejected):
                market_data.get_quote("AAPL")

    def test_the_log_names_the_feed_being_asked_for(self, caplog):
        """The usual cause is asking for SIP on the free plan, so the log
        says which feed the request used."""
        with caplog.at_level("ERROR"), responding(403, text="subscription"):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("AAPL")
        assert config.ALPACA_DATA_FEED in caplog.text

    def test_the_free_plan_feed_is_what_gets_requested(self):
        """Left to default, Alpaca serves SIP and refuses a free key."""
        seen = {}

        def get(url, **kwargs):
            seen.update(kwargs.get("params") or {})
            return FakeResponse(200, asset_payload() if "/assets/" in url
                                else snapshot_payload())

        with patch.object(market_data._session, "get", side_effect=get):
            market_data.get_quote("AAPL")
        assert seen.get("feed") == "iex"


class TestTheRateLimitIs429:
    def test_it_raises_rather_than_returning_nothing(self):
        with responding(429, text="too many requests"):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("AAPL")

    def test_the_log_mentions_the_limit(self, caplog):
        with caplog.at_level("WARNING"), responding(429, text="slow down"):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("AAPL")
        assert "429" in caplog.text and "200 requests" in caplog.text

    def test_retry_after_is_logged_when_given(self, caplog):
        with caplog.at_level("WARNING"), responding(
                429, text="slow down", headers={"Retry-After": "30"}):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("AAPL")
        assert "Retry-After 30" in caplog.text

    def test_a_rate_limited_refresh_reports_failure_not_success(self, user):
        """The figure people act on must not silently stop updating."""
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        portfolio.record_purchase(user, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("1"))
        with responding(429, text="slow down"):
            report = operations.refresh_prices(user)
        assert report.failed == 1 and report.updated == 0


class TestOtherStatusCodes:
    # 404 is deliberately absent: on the catalogue it means "no such
    # ticker", which is an answer rather than a fault. See the test below.
    @pytest.mark.parametrize("status", [400, 500, 502, 503])
    def test_they_all_raise(self, status):
        with responding(status, text="something"):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("AAPL")

    def test_a_400_is_logged_with_alpacas_explanation(self, caplog):
        with caplog.at_level("WARNING"), responding(400, text="invalid symbol"):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("AAPL")
        assert "invalid symbol" in caplog.text

    def test_a_404_on_the_catalogue_is_not_an_error(self):
        """It is the one unambiguous "no such ticker" Alpaca offers, and
        the only endpoint where 404 means that rather than a fault."""
        with responding(404, text="not found"):
            assert market_data.resolve_symbol("ZZZZ") is None

    def test_a_body_that_is_not_json_raises(self, caplog):
        """A proxy or an error page can answer 200 with HTML."""
        response = FakeResponse(200, payload=None, text="<html>gateway</html>")
        with patch.object(market_data._session, "get", return_value=response):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("AAPL")


class TestTimeouts:
    def test_every_call_carries_one(self):
        seen = {}

        def get(url, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return FakeResponse(200, asset_payload() if "/assets/" in url
                                else snapshot_payload())

        with patch.object(market_data._session, "get", side_effect=get):
            market_data.get_quote("AAPL")
        assert seen["timeout"] is not None
        connect, read = seen["timeout"]
        assert connect > 0 and read > 0

    def test_a_timeout_is_an_outage_not_an_empty_result(self):
        import requests

        with patch.object(market_data._session, "get",
                          side_effect=requests.Timeout("too slow")):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("AAPL")

    def test_a_connection_failure_is_an_outage(self):
        import requests

        with patch.object(market_data._session, "get",
                          side_effect=requests.ConnectionError("no route")):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_price_history("AAPL")

    def test_the_network_error_never_reaches_the_reader(self):
        import requests

        with patch.object(market_data._session, "get",
                          side_effect=requests.ConnectionError("getaddrinfo failed")):
            try:
                market_data.get_quote("AAPL")
            except MarketDataUnavailable as exc:
                assert "getaddrinfo" not in str(exc)

    def test_history_gets_the_longer_limit(self):
        """A full history is bigger than a quote and is allowed longer."""
        seen = []

        def get(url, **kwargs):
            seen.append((url, kwargs.get("timeout")))
            return FakeResponse(200, asset_payload() if "/assets/" in url
                                else bars_payload(closes=None))

        with patch.object(market_data._session, "get", side_effect=get):
            market_data.get_price_history("AAPL")
        bars_call = [t for url, t in seen if "/bars" in url][0]
        assert bars_call[1] == config.MARKET_HISTORY_TIMEOUT


class TestEmptyIsNotTheSameAsBroken:
    """The trap this API sets: a symbol that does not exist and a real one
    with no trading both come back as 200 with `"bars": null`."""

    def test_null_bars_for_a_real_symbol_is_simply_empty(self):
        with route_alpaca(asset=asset_payload(),
                          bars_response=bars_payload(closes=None)):
            assert market_data.get_price_history("AAPL") == []

    def test_an_unknown_symbol_raises_rather_than_looking_empty(self):
        """Otherwise a typo reports "0 new days" and looks like success."""
        with route_alpaca(asset=None):
            with pytest.raises(UnknownSymbol):
                market_data.get_price_history("ZZZZ")

    def test_the_loader_reports_nothing_rather_than_zero_during_an_outage(self, db):
        from portfolio_tracker.models import history

        with route_alpaca(asset=asset_payload(), status=500):
            with pytest.raises(MarketDataUnavailable):
                history.load_history_for_symbol("AAPL")

    def test_a_genuinely_empty_range_loads_zero_rows(self, db):
        from portfolio_tracker.models import history

        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        with route_alpaca(asset=asset_payload(),
                          bars_response=bars_payload(closes=None)):
            assert history.load_history_for_symbol("AAPL") == 0

    def test_bars_are_parsed_into_decimals(self):
        with route_alpaca(asset=asset_payload(),
                          bars_response=bars_payload(closes=[101.5, 102.25])):
            got = market_data.get_price_history("AAPL")
        assert [bar.close for bar in got] == [Decimal("101.5"), Decimal("102.25")]
        assert all(bar.ts.tzinfo is not None for bar in got)

    def test_pagination_is_followed(self):
        pages = [
            FakeResponse(200, asset_payload()),
            FakeResponse(200, bars_payload(closes=[1.0], next_page_token="more")),
            FakeResponse(200, bars_payload(closes=[2.0], start="2026-09-16T04:00:00Z")),
        ]

        def get(url, **kwargs):
            if "/assets/" in url:
                return pages[0]
            return pages.pop(1) if len(pages) > 2 else pages[-1]

        with patch.object(market_data._session, "get", side_effect=get):
            got = market_data.get_price_history("AAPL")
        assert [bar.close for bar in got] == [Decimal("1.0"), Decimal("2.0")]


class TestQuoteCache:
    def test_a_repeat_lookup_does_not_ask_again(self):
        calls = []

        def get(url, **kwargs):
            calls.append(url)
            return FakeResponse(200, asset_payload() if "/assets/" in url
                                else snapshot_payload())

        with patch.object(market_data._session, "get", side_effect=get):
            first = market_data.get_quote("TEN")
            second = market_data.get_quote("ten ")
        assert first == second
        assert sum(1 for url in calls if "/snapshot" in url) == 1

    def test_fresh_bypasses_the_cache(self):
        snapshots = [snapshot_payload(price="10"), snapshot_payload(price="11")]

        def get(url, **kwargs):
            if "/assets/" in url:
                return FakeResponse(200, asset_payload())
            return FakeResponse(200, snapshots.pop(0) if snapshots
                                else snapshot_payload(price="11"))

        with patch.object(market_data._session, "get", side_effect=get):
            assert market_data.get_quote("TEN")[0] == Decimal("10.0")
            assert market_data.get_quote("TEN", fresh=True)[0] == Decimal("11.0")
            assert market_data.get_quote("TEN")[0] == Decimal("11.0")

    def test_entries_expire(self, monkeypatch):
        monkeypatch.setattr(config, "QUOTE_CACHE_SECONDS", 0.05)
        with route_alpaca(asset=asset_payload(), snapshot=snapshot_payload()):
            market_data.get_quote("TEN")
            time.sleep(0.1)
            assert market_data._quotes["TEN"][0] < time.monotonic()

    def test_a_zero_window_disables_caching(self, monkeypatch):
        monkeypatch.setattr(config, "QUOTE_CACHE_SECONDS", 0)
        with route_alpaca(asset=asset_payload(), snapshot=snapshot_payload()):
            market_data.get_quote("TEN")
        assert market_data._quotes == {}

    def test_failures_are_not_cached(self):
        with responding(500, text="server error"):
            with pytest.raises(MarketDataUnavailable):
                market_data.get_quote("TEN")
        assert "TEN" not in market_data._quotes

    def test_the_cache_is_bounded(self):
        with route_alpaca(asset=asset_payload(), snapshot=snapshot_payload()):
            for i in range(market_data._QUOTE_CACHE_LIMIT + 40):
                market_data.get_quote(f"S{i}")
        assert len(market_data._quotes) <= market_data._QUOTE_CACHE_LIMIT

    def test_concurrent_lookups_are_safe(self):
        with route_alpaca(asset=asset_payload(), snapshot=snapshot_payload()):
            def work(n):
                for i in range(20):
                    market_data.get_quote(f"T{(n + i) % 7}")

            threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(20)
                assert not t.is_alive()
        assert len(market_data._quotes) <= market_data._QUOTE_CACHE_LIMIT


class TestTheCatalogueIsCachedSeparately:
    def test_a_name_is_not_re_fetched_with_every_quote(self):
        """Names change with corporate actions, not seconds. Re-asking
        would double every quote's cost against a 200/minute limit."""
        calls = []

        def get(url, **kwargs):
            calls.append(url)
            return FakeResponse(200, asset_payload() if "/assets/" in url
                                else snapshot_payload())

        with patch.object(market_data._session, "get", side_effect=get):
            market_data.get_quote("AAPL", fresh=True)
            market_data.get_quote("AAPL", fresh=True)
        assert sum(1 for url in calls if "/assets/" in url) == 1
        assert sum(1 for url in calls if "/snapshot" in url) == 2

    def test_clearing_the_cache_clears_both(self):
        with route_alpaca(asset=asset_payload(), snapshot=snapshot_payload()):
            market_data.get_quote("AAPL")
        market_data.clear_quote_cache()
        assert market_data._quotes == {} and market_data._assets == {}


class TestWhoGetsAFreshPrice:
    def spy(self):
        return patch.object(
            market_data, "get_quote",
            return_value=(Decimal("100"), "Test Co", Decimal("98")))

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
        with patch.object(market_data, "get_live_price",
                          return_value=Decimal("100")) as live:
            operations.sell(user, "TST", "1")
        assert live.call_args.kwargs.get("fresh") is True

    def test_refreshing_always_fetches_fresh_prices(self, user):
        stocks.upsert_stock("TST", "Test Co", Decimal("100"))
        portfolio.record_purchase(user, "TST", "Test Co", Decimal("100"), Decimal("2"))
        with self.spy() as get_quote:
            operations.refresh_prices(user)
        assert get_quote.call_args.kwargs.get("fresh") is True


class TestAnUnconfiguredInstall:
    """The first thing a fresh checkout hits, before any keys exist."""

    @pytest.fixture
    def no_keys(self, monkeypatch):
        monkeypatch.setattr(config, "ALPACA_API_KEY_ID", None)
        monkeypatch.setattr(config, "ALPACA_API_SECRET_KEY", None)

    def test_a_lookup_says_what_to_do(self, user, no_keys):
        with pytest.raises(operations.ValidationError) as caught:
            operations.look_up(user, "AAPL")
        message = str(caught.value)
        assert "not configured" in message
        assert ".env" in message

    def test_it_does_not_tell_them_to_retry(self, user, no_keys):
        """Retrying is the one thing that cannot possibly help."""
        with pytest.raises(operations.ValidationError) as caught:
            operations.look_up(user, "AAPL")
        assert "try again" not in str(caught.value).lower()

    def test_the_page_shows_it_rather_than_a_500(self, client, no_keys):
        response = client.get("/stock/AAPL", follow_redirects=True)
        assert response.status_code == 200
        assert b"not configured" in response.data

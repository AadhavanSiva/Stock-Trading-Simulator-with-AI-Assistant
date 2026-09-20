"""Reading Alpaca's responses: quotes, names, and the previous close."""
from decimal import Decimal

import pytest
from conftest import asset_payload, route_alpaca, snapshot_payload

from portfolio_tracker.services import market_data


class TestGetQuote:
    def test_returns_price_name_and_previous_close(self):
        with route_alpaca(asset=asset_payload(), snapshot=snapshot_payload()):
            assert market_data.get_quote("AAPL") == (
                Decimal("100.0"), "Apple Inc.", Decimal("90.0")
            )

    def test_the_price_keeps_its_exact_decimal_value(self):
        """Never through a binary float: every price column is NUMERIC."""
        with route_alpaca(asset=asset_payload(),
                          snapshot=snapshot_payload(price="332.27")):
            price = market_data.get_quote("AAPL")[0]
        assert price == Decimal("332.27")
        assert str(price) == "332.27"

    def test_an_unknown_symbol_has_no_price_and_is_not_an_error(self):
        """The catalogue answered "no such ticker" — that is an answer."""
        with route_alpaca(asset=None):
            assert market_data.get_quote("ZZZZ") == (None, "ZZZZ", None)

    def test_the_symbol_is_normalised(self):
        with route_alpaca(asset=asset_payload(), snapshot=snapshot_payload()):
            assert market_data.get_quote("  aapl  ")[0] == Decimal("100.0")

    def test_the_name_comes_from_the_catalogue(self):
        """Alpaca's snapshot carries no company name; the asset does."""
        with route_alpaca(asset=asset_payload(name="Microsoft Corporation"),
                          snapshot=snapshot_payload()):
            assert market_data.get_quote("MSFT")[1] == "Microsoft Corporation"

    def test_a_nameless_asset_falls_back_to_the_symbol(self):
        with route_alpaca(asset=asset_payload(name=None),
                          snapshot=snapshot_payload()):
            assert market_data.get_quote("AAPL")[1] == "AAPL"

    def test_an_empty_snapshot_has_no_price(self):
        with route_alpaca(asset=asset_payload(), snapshot={}):
            assert market_data.get_quote("AAPL") == (None, "Apple Inc.", None)


class TestWhichPriceIsUsed:
    """Outside market hours the snapshot's sections fall away one by one,
    and the order they are preferred in is what keeps a page showing a
    real number rather than a dash."""

    def test_a_trade_is_preferred(self):
        snapshot = snapshot_payload(price="100")
        snapshot["minuteBar"] = {"c": 99.0}
        with route_alpaca(asset=asset_payload(), snapshot=snapshot):
            assert market_data.get_quote("AAPL")[0] == Decimal("100.0")

    def test_the_minute_bar_stands_in_when_there_is_no_trade(self):
        snapshot = snapshot_payload(price=None)
        snapshot["minuteBar"] = {"c": 99.5}
        with route_alpaca(asset=asset_payload(), snapshot=snapshot):
            assert market_data.get_quote("AAPL")[0] == Decimal("99.5")

    def test_the_previous_close_is_the_last_resort(self):
        """Before the first trade of the day there is nothing newer."""
        snapshot = snapshot_payload(price=None, previous_close="88")
        with route_alpaca(asset=asset_payload(), snapshot=snapshot):
            price, _, previous = market_data.get_quote("AAPL")
        assert price == Decimal("88.0") and previous == Decimal("88.0")


class TestPreviousClose:
    def test_it_comes_from_the_previous_session(self):
        with route_alpaca(asset=asset_payload(),
                          snapshot=snapshot_payload(previous_close="90")):
            assert market_data.get_previous_close("AAPL") == Decimal("90.0")

    def test_it_is_never_taken_from_todays_bar(self):
        """dailyBar is today. Comparing today against itself would report
        every stock as perfectly flat."""
        snapshot = snapshot_payload(price="100", previous_close=None)
        snapshot["dailyBar"] = {"c": 100.0}
        with route_alpaca(asset=asset_payload(), snapshot=snapshot):
            assert market_data.get_previous_close("AAPL") is None

    def test_a_missing_previous_close_is_none_not_zero(self):
        with route_alpaca(asset=asset_payload(),
                          snapshot=snapshot_payload(previous_close=None)):
            assert market_data.get_quote("AAPL")[2] is None


class TestTheCatalogue:
    def test_a_listed_tradable_symbol_passes(self):
        with route_alpaca(asset=asset_payload()):
            assert market_data.symbol_is_tradable("AAPL") == (True, None)

    def test_an_unknown_symbol_is_refused_by_name(self):
        with route_alpaca(asset=None):
            ok, reason = market_data.symbol_is_tradable("ZZZZ")
        assert ok is False and "ZZZZ" in reason

    def test_an_untradable_symbol_is_refused(self):
        with route_alpaca(asset=asset_payload(tradable=False)):
            ok, reason = market_data.symbol_is_tradable("AAPL")
        assert ok is False and "not currently tradable" in reason

    def test_an_inactive_symbol_is_refused(self):
        with route_alpaca(asset=asset_payload(status="inactive")):
            assert market_data.symbol_is_tradable("AAPL")[0] is False

    def test_a_non_equity_is_refused(self):
        """Alpaca lists crypto and options this app does not handle."""
        with route_alpaca(asset=asset_payload(asset_class="crypto")):
            ok, reason = market_data.symbol_is_tradable("BTCUSD")
        assert ok is False and "not a US-listed stock" in reason

    def test_an_unknown_symbol_resolves_to_none(self):
        with route_alpaca(asset=None):
            assert market_data.resolve_symbol("ZZZZ") is None

    def test_an_empty_symbol_asks_nothing(self):
        assert market_data.resolve_symbol("") is None
        assert market_data.resolve_symbol(None) is None


class TestTimestamps:
    """Alpaca sends RFC-3339 with nanoseconds and a trailing Z, neither of
    which Python 3.10's fromisoformat reads."""

    @pytest.mark.parametrize("raw,expected_hour", [
        ("2026-09-18T19:59:00Z", 19),
        ("2026-09-18T19:59:00.123456789Z", 19),
        ("2026-09-18T19:59:00.123Z", 19),
        ("2026-09-18T19:59:00+00:00", 19),
        ("2026-09-18T15:59:00-04:00", 15),
    ])
    def test_it_reads_the_shapes_alpaca_sends(self, raw, expected_hour):
        moment = market_data._timestamp(raw)
        assert moment is not None
        assert moment.hour == expected_hour
        assert moment.tzinfo is not None, "an offset must never be lost"

    def test_nanoseconds_do_not_raise(self):
        assert market_data._timestamp("2026-09-18T19:59:00.999999999Z") is not None

    def test_nonsense_is_none_rather_than_an_exception(self):
        """A bad timestamp should cost one bar, not the whole request."""
        assert market_data._timestamp("not a time") is None
        assert market_data._timestamp("") is None
        assert market_data._timestamp(None) is None

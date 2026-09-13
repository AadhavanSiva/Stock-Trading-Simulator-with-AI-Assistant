"""Market data tests. yfinance is mocked — no network, no flakiness."""
from decimal import Decimal
from unittest.mock import patch

import pytest

from portfolio_tracker.services import market_data


class FakeTicker:
    def __init__(self, info):
        self.info = info


def with_info(info):
    return patch.object(market_data.yf, "Ticker", return_value=FakeTicker(info))


class TestToDecimal:
    def test_float_does_not_pick_up_binary_dust(self):
        """Decimal(332.27) would be 332.2700000000000102...; str() keeps it exact."""
        assert market_data._to_decimal(332.27) == Decimal("332.27")

    @pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "abc"])
    def test_unusable_values_become_none(self, bad):
        assert market_data._to_decimal(bad) is None


class TestGetQuote:
    def test_returns_decimal_price_and_name(self):
        with with_info({"currentPrice": 332.27, "longName": "Apple Inc."}):
            price, name = market_data.get_quote("AAPL")
        assert price == Decimal("332.27")
        assert isinstance(price, Decimal)
        assert name == "Apple Inc."

    def test_falls_back_to_regular_market_price(self):
        with with_info({"regularMarketPrice": 100.5, "longName": "X"}):
            assert market_data.get_quote("X")[0] == Decimal("100.5")

    def test_missing_price_is_none_not_a_crash(self):
        with with_info({"longName": "Thin Co"}):
            assert market_data.get_quote("THIN")[0] is None

    def test_name_falls_back_to_the_symbol(self):
        with with_info({"currentPrice": 1.0}):
            assert market_data.get_quote("XYZ")[1] == "XYZ"

    def test_null_longname_falls_back_instead_of_storing_none(self):
        """`.get('longName', symbol)` returned None when the key existed but was null."""
        with with_info({"currentPrice": 1.0, "longName": None, "shortName": "Short"}):
            assert market_data.get_quote("XYZ")[1] == "Short"

    def test_empty_info_is_handled(self):
        with with_info({}):
            assert market_data.get_quote("XYZ") == (None, "XYZ")

    def test_none_info_is_handled(self):
        with with_info(None):
            assert market_data.get_quote("XYZ") == (None, "XYZ")

"""Price-history tests. The yfinance dataframe is faked."""
from decimal import Decimal
from unittest.mock import patch

import pandas as pd

from portfolio_tracker.models import history, stocks


def frame(rows):
    """Build a yfinance-shaped OHLCV frame indexed by timestamp."""
    index = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[2] for r in rows],
            "Low": [r[3] for r in rows],
            "Close": [r[4] for r in rows],
            "Volume": [r[5] for r in rows],
        },
        index=index,
    )


def with_history(df):
    return patch.object(history, "get_price_history", return_value=df)


class TestHelpers:
    def test_nan_volume_becomes_none(self):
        """Regression: int(NaN) raised ValueError and aborted the whole load."""
        assert history._volume(float("nan")) is None

    def test_volume_is_an_int(self):
        assert history._volume(1234.0) == 1234

    def test_prices_are_exact_decimals(self):
        assert history._price(150.25) == Decimal("150.25")

    def test_nan_price_becomes_none(self):
        assert history._price(float("nan")) is None


class TestLoadHistory:
    def test_loads_rows(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("200"))
        df = frame([
            ("2026-01-02", 1.0, 2.0, 0.5, 1.5, 1000),
            ("2026-01-03", 1.5, 2.5, 1.0, 2.0, 2000),
        ])
        with with_history(df):
            assert history.load_history_for_symbol("AAPL") == 2

    def test_rerun_inserts_nothing_new(self, db):
        """Returns rows actually inserted; len(records) reported phantom work."""
        stocks.upsert_stock("AAPL", "Apple", Decimal("200"))
        df = frame([("2026-01-02", 1.0, 2.0, 0.5, 1.5, 1000)])
        with with_history(df):
            assert history.load_history_for_symbol("AAPL") == 1
            assert history.load_history_for_symbol("AAPL") == 0

    def test_a_nan_volume_row_still_loads(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("200"))
        df = frame([("2026-01-02", 1.0, 2.0, 0.5, 1.5, float("nan"))])
        with with_history(df):
            assert history.load_history_for_symbol("AAPL") == 1

    def test_rows_with_no_close_are_skipped(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("200"))
        df = frame([
            ("2026-01-02", 1.0, 2.0, 0.5, float("nan"), 100),
            ("2026-01-03", 1.0, 2.0, 0.5, 1.5, 100),
        ])
        with with_history(df):
            assert history.load_history_for_symbol("AAPL") == 1

    def test_empty_frame_is_a_no_op(self, db):
        with with_history(pd.DataFrame()):
            assert history.load_history_for_symbol("AAPL") == 0

    def test_none_frame_is_a_no_op(self, db):
        with with_history(None):
            assert history.load_history_for_symbol("AAPL") == 0


class TestAggregates:
    def test_high_low(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("200"))
        df = frame([
            ("2026-01-02", 1.0, 2.0, 0.5, 1.5, 100),
            ("2026-01-03", 1.0, 2.0, 0.5, 3.5, 100),
        ])
        with with_history(df):
            history.load_history_for_symbol("AAPL")

        symbol, high, low = history.get_high_low()[0]
        assert (symbol, high, low) == ("AAPL", Decimal("3.5"), Decimal("1.5"))

    def test_averages_ignore_rows_outside_the_window(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("200"))
        old = pd.Timestamp.today().normalize() - pd.Timedelta(days=400)
        df = frame([(old.strftime("%Y-%m-%d"), 1.0, 2.0, 0.5, 1.5, 100)])
        with with_history(df):
            history.load_history_for_symbol("AAPL")

        assert history.get_recent_averages(days=30) == []

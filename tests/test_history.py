"""Price-history tests. The market data provider's bars are faked."""
from datetime import datetime, time as clock, timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from portfolio_tracker.models import history, stocks
from portfolio_tracker.services.market_data import Bar

EXCHANGE = ZoneInfo("America/New_York")


def frame(rows):
    """Bars from (date, open, high, low, close, volume) tuples.

    Stamped at the session open in exchange time and converted to UTC, as
    Alpaca sends them — which is also what makes the loader's conversion
    back to a trading date worth testing rather than incidental.
    """
    made = []
    for day, opening, high, low, close, volume in rows:
        session = datetime.combine(
            datetime.strptime(day, "%Y-%m-%d").date(), clock(9, 30), EXCHANGE
        )
        made.append(Bar(
            ts=session.astimezone(timezone.utc),
            open=_maybe(opening), high=_maybe(high), low=_maybe(low),
            close=_maybe(close), volume=_maybe_int(volume),
        ))
    return made


def _maybe(value):
    """A price as Decimal, or None where the old frames used NaN."""
    if value is None:
        return None
    value = Decimal(str(value))
    return value if value.is_finite() else None


def _maybe_int(value):
    if value is None:
        return None
    return None if value != value else int(value)      # NaN != NaN


def with_history(bars):
    return patch.object(history, "get_price_history", return_value=bars)


class TestHelpers:
    def test_absent_volume_becomes_none(self):
        """Regression: int(NaN) raised ValueError and aborted the whole
        load. Alpaca omits the field rather than sending NaN, so None is
        now the shape that arrives, and it must be just as harmless."""
        assert history._volume(None) is None

    def test_volume_is_an_int(self):
        assert history._volume(1234.0) == 1234

    def test_prices_are_exact_decimals(self):
        assert history._price(150.25) == Decimal("150.25")

    def test_absent_price_becomes_none(self):
        assert history._price(None) is None

    def test_a_non_finite_price_becomes_none(self):
        """Belt and braces: the parser filters these, but a NaN reaching
        here must never be written to a NUMERIC column."""
        assert history._price(Decimal("NaN")) is None


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
        df = frame([("2026-01-02", 1.0, 2.0, 0.5, 1.5, None)])
        with with_history(df):
            assert history.load_history_for_symbol("AAPL") == 1

    def test_rows_with_no_close_are_skipped(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("200"))
        df = frame([
            ("2026-01-02", 1.0, 2.0, 0.5, None, 100),
            ("2026-01-03", 1.0, 2.0, 0.5, 1.5, 100),
        ])
        with with_history(df):
            assert history.load_history_for_symbol("AAPL") == 1

    def test_no_bars_is_a_no_op(self, db):
        with with_history([]):
            assert history.load_history_for_symbol("AAPL") == 0

    def test_none_is_a_no_op(self, db):
        with with_history(None):
            assert history.load_history_for_symbol("AAPL") == 0


class TestAggregates:
    def test_high_low(self, db):
        """Dated relative to today, because the query is windowed: fixed
        dates would pass until they aged past the window and then fail for
        a reason that has nothing to do with high and low."""
        from datetime import timedelta

        today = datetime.now(EXCHANGE).date()
        stocks.upsert_stock("AAPL", "Apple", Decimal("200"))
        df = frame([
            ((today - timedelta(days=3)).strftime("%Y-%m-%d"), 1.0, 2.0, 0.5, 1.5, 100),
            ((today - timedelta(days=2)).strftime("%Y-%m-%d"), 1.0, 2.0, 0.5, 3.5, 100),
        ])
        with with_history(df):
            history.load_history_for_symbol("AAPL")

        symbol, high, low = history.get_high_low()[0]
        assert (symbol, high, low) == ("AAPL", Decimal("3.5"), Decimal("1.5"))

    def test_averages_ignore_rows_outside_the_window(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("200"))
        from datetime import timedelta

        old = datetime.now(EXCHANGE).date() - timedelta(days=400)
        df = frame([(old.strftime("%Y-%m-%d"), 1.0, 2.0, 0.5, 1.5, 100)])
        with with_history(df):
            history.load_history_for_symbol("AAPL")

        assert history.get_recent_averages(days=30) == []


class TestTheRecentRangeIsActuallyRecent:
    """Regression: get_high_low spanned every stored day, so the page put a
    pre-split $1.05 next to a $364 Tesla under a heading reading "the last
    30 days". The same table had this bug once before, when the 1D panel
    reported a 1980 split-adjusted $0.04 as the one-day low.
    """

    def _load(self, db, rows):
        stocks.upsert_stock("TSLA", "Tesla, Inc.", Decimal("364"))
        with with_history(frame(rows)):
            history.load_history_for_symbol("TSLA")

    def _days_ago(self, n):
        from datetime import timedelta

        return (datetime.now(EXCHANGE).date() - timedelta(days=n)).strftime("%Y-%m-%d")

    def test_an_ancient_low_is_not_reported_as_recent(self, db):
        self._load(db, [
            (self._days_ago(4000), 1.0, 1.1, 1.0, 1.05, 100),   # pre-split
            (self._days_ago(5), 360.0, 370.0, 355.0, 364.0, 100),
            (self._days_ago(2), 360.0, 370.0, 355.0, 359.0, 100),
        ])
        symbol, high, low = history.get_high_low()[0]
        assert low == Decimal("359.0"), "an all-time low leaked into the window"
        assert high == Decimal("364.0")

    def test_the_window_matches_the_average_beside_it(self, db):
        """The two columns sit in one row and must cover the same days."""
        rows = [(self._days_ago(n), 100.0, 100.0, 100.0, float(100 + n), 100)
                for n in (2, 5, 10, 200)]
        self._load(db, rows)

        averages = dict(history.get_recent_averages())
        _, high, low = history.get_high_low()[0]
        # The 200-day-old bar is outside both, so neither sees its 300.
        assert averages["TSLA"] < Decimal("300")
        assert high < Decimal("300") and low < Decimal("300")

    def test_both_windows_come_from_one_constant(self, db):
        """So they cannot drift apart the way they did."""
        rows = [(self._days_ago(n), 1.0, 1.0, 1.0, float(n), 100)
                for n in (1, history.RECENT_DAYS + 20)]
        self._load(db, rows)
        assert len(history.get_recent_averages()) == len(history.get_high_low())

    def test_a_wider_window_reaches_further_back(self, db):
        self._load(db, [
            (self._days_ago(100), 1.0, 1.1, 1.0, 5.0, 100),
            (self._days_ago(2), 360.0, 370.0, 355.0, 364.0, 100),
        ])
        assert history.get_high_low(days=30)[0][2] == Decimal("364.0")
        assert history.get_high_low(days=365)[0][2] == Decimal("5.0")

    def test_a_symbol_with_nothing_recent_drops_out(self, db):
        """Rather than reporting a stale range as current."""
        self._load(db, [(self._days_ago(400), 1.0, 1.1, 1.0, 5.0, 100)])
        assert history.get_high_low() == []

    def test_the_page_labels_the_window(self, client, user):
        from portfolio_tracker.models import portfolio

        stocks.upsert_stock("TSLA", "Tesla, Inc.", Decimal("364"))
        portfolio.record_purchase(user, "TSLA", "Tesla, Inc.",
                                  Decimal("364"), Decimal("1"))
        page = client.get("/history").data
        assert b"30-day high" in page and b"30-day low" in page
        assert b">Highest<" not in page and b">Lowest<" not in page

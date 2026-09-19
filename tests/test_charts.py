"""Price chart tests: the geometry, the stored series, and the stock page."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest

from portfolio_tracker import charts
from portfolio_tracker.db import cursor
from portfolio_tracker.models import history, portfolio, stocks
from portfolio_tracker.services import market_data


def text(response):
    return response.get_data(as_text=True)


def store_daily(symbol, closes, start=None):
    """Write one close per day, oldest first."""
    start = start or (date.today() - timedelta(days=len(closes) - 1))
    stocks.upsert_stock(symbol, f"{symbol} Inc.", Decimal(str(closes[-1])))
    with cursor(commit=True) as cur:
        for offset, close in enumerate(closes):
            cur.execute(
                "INSERT INTO price_history (symbol, date, close) VALUES (%s, %s, %s)",
                (symbol, start + timedelta(days=offset), Decimal(str(close))),
            )


class TestGeometry:
    def test_empty_series_returns_none(self):
        """The page renders an explanation instead of an empty frame."""
        assert charts.build([]) is None

    def test_all_none_values_count_as_empty(self):
        assert charts.build([("a", None), ("b", None)]) is None

    def test_highest_value_is_drawn_at_the_top(self):
        """SVG y grows downward, so a naive mapping draws the chart upside down."""
        chart = charts.build([("a", 10), ("b", 20)], height=100, pad=0)
        low_point, high_point = chart.points
        assert high_point["y"] < low_point["y"]
        assert high_point["y"] == 0
        assert low_point["y"] == 100

    def test_points_span_the_full_width(self):
        chart = charts.build([("a", 1), ("b", 2), ("c", 3)], width=300)
        assert chart.points[0]["x"] == 0
        assert chart.points[-1]["x"] == 300

    def test_flat_series_does_not_divide_by_zero(self):
        """high == low makes (value - low) / (high - low) a division by zero."""
        chart = charts.build([("a", 50), ("b", 50), ("c", 50)], height=200)
        assert all(p["y"] == 100 for p in chart.points)

    def test_single_point_still_yields_a_valid_path(self):
        """'M' followed by nothing is malformed SVG."""
        chart = charts.build([("a", 42)], width=200)
        assert chart.path.startswith("M ")
        assert " L " in chart.path
        assert chart.count == 1

    def test_change_and_percentage(self):
        chart = charts.build([("a", 100), ("b", 90), ("c", 125)])
        assert chart.first == Decimal("100")
        assert chart.last == Decimal("125")
        assert chart.change == Decimal("25")
        assert chart.change_pct == Decimal("25")

    def test_values_stay_decimal(self):
        """The figures shown beside the chart must not pass through float."""
        chart = charts.build([("a", Decimal("332.27")), ("b", Decimal("335.10"))])
        assert isinstance(chart.change, Decimal)
        assert chart.change == Decimal("2.83")

    def test_area_path_is_closed(self):
        chart = charts.build([("a", 1), ("b", 3), ("c", 2)])
        assert chart.area_path.endswith("Z")

    def test_zero_first_value_does_not_divide_by_zero(self):
        chart = charts.build([("a", 0), ("b", 5)])
        assert chart.change_pct == Decimal(0)


class TestThinning:
    """Long series are reduced for drawing; the figures never are."""

    def long_series(self, n):
        return [(i, 100 + (i % 50)) for i in range(n)]

    def test_short_series_is_drawn_in_full(self):
        chart = charts.build(self.long_series(300))
        assert chart.plotted == chart.count == 300

    def test_long_series_is_capped(self):
        chart = charts.build(self.long_series(11_529))
        assert chart.count == 11_529
        assert chart.plotted <= charts.MAX_PLOTTED

    def test_figures_come_from_the_full_series(self):
        """Thinning must not move the numbers printed beside the chart."""
        data = self.long_series(11_529)
        data[5_000] = (5_000, 1)       # a lone dip deep in the middle
        data[7_000] = (7_000, 999)     # and a lone spike
        chart = charts.build(data)
        assert chart.low == Decimal("1")
        assert chart.high == Decimal("999")
        assert chart.first == Decimal("100")

    def test_a_lone_spike_survives_thinning_visually(self):
        """Min/max bucketing keeps extremes; a stride sample would lose them."""
        data = [(i, 100) for i in range(10_000)]
        data[6_543] = (6_543, 500)
        chart = charts.build(data)
        assert any(p["value"] == Decimal("500") for p in chart.points)

    def test_first_and_last_points_are_always_kept(self):
        data = self.long_series(9_999)
        chart = charts.build(data)
        assert chart.points[0]["label"] == 0
        assert chart.points[-1]["label"] == 9_998

    def test_thinned_points_stay_in_time_order(self):
        chart = charts.build(self.long_series(5_000))
        labels = [p["label"] for p in chart.points]
        assert labels == sorted(labels)

    def test_average_is_exact_to_the_cent(self):
        chart = charts.build([("a", Decimal("10.00")), ("b", Decimal("20.01"))])
        assert chart.average == Decimal("15.01")


class TestRanges:
    def test_all_seven_ranges_exist(self):
        assert list(charts.RANGES) == ["1d", "5d", "1m", "3m", "6m", "1y", "all"]

    def test_only_the_short_ranges_are_intraday(self):
        intraday = [key for key, spec in charts.RANGES.items() if spec["intraday"]]
        assert intraday == ["1d", "5d"]

    @pytest.mark.parametrize("raw,expected", [
        ("3m", "3m"), ("3M", "3m"), (" 1y ", "1y"),
        ("", charts.DEFAULT_RANGE), (None, charts.DEFAULT_RANGE),
        ("10y", charts.DEFAULT_RANGE), ("<script>", charts.DEFAULT_RANGE),
    ])
    def test_unknown_ranges_fall_back_quietly(self, raw, expected):
        assert charts.normalise_range(raw) == expected


class TestStoredSeries:
    def test_series_is_oldest_first(self, db):
        store_daily("AAPL", [10, 20, 30])
        values = [close for _, close in history.get_series("AAPL")]
        assert values == [Decimal("10"), Decimal("20"), Decimal("30")]

    def test_since_trims_the_window(self, db):
        store_daily("AAPL", [10, 20, 30, 40])
        since = date.today() - timedelta(days=1)
        values = [close for _, close in history.get_series("AAPL", since)]
        assert values == [Decimal("30"), Decimal("40")]

    def test_range_stats(self, db):
        store_daily("AAPL", [10, 30, 20])
        low, high, average, count = history.get_range_stats("AAPL")
        assert (low, high, average, count) == (Decimal("10"), Decimal("30"), Decimal("20.00"), 3)

    def test_range_stats_with_no_data(self, db):
        low, high, average, count = history.get_range_stats("NOPE")
        assert (low, high, average, count) == (None, None, None, 0)

    def test_series_is_scoped_to_one_symbol(self, db):
        store_daily("AAPL", [10, 20])
        store_daily("MSFT", [500, 510])
        assert len(history.get_series("AAPL")) == 2


class TestInsertCounts:
    """Regression: execute_values pages inserts 100 rows at a time and
    cur.rowcount only reports the final page, so a large load said "29 new
    days" after storing 11,529."""

    def test_daily_count_is_exact_beyond_one_page(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("100"))
        n = 257  # deliberately not a multiple of 100
        frame = pd.DataFrame(
            {"Open": [1.0] * n, "High": [1.0] * n, "Low": [1.0] * n,
             "Close": [1.0] * n, "Volume": [1] * n},
            index=pd.date_range(end=pd.Timestamp.today(), periods=n, freq="D"),
        )
        with patch.object(history, "get_price_history", return_value=frame):
            assert history.load_history_for_symbol("AAPL") == n
            # A second run inserts nothing and must say so.
            assert history.load_history_for_symbol("AAPL") == 0

    def test_intraday_count_is_exact_beyond_one_page(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("100"))
        n = 234
        frame = pd.DataFrame(
            {"Close": [1.0] * n},
            index=pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n, freq="min"),
        )
        with patch.object(history, "get_intraday", return_value=frame):
            assert history.load_intraday_for_symbol("AAPL") == n

    def test_partial_overlap_counts_only_new_rows(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("100"))
        index = pd.date_range(end=pd.Timestamp.today(), periods=150, freq="D")
        cols = lambda k: {"Open": [1.0]*k, "High": [1.0]*k, "Low": [1.0]*k,
                          "Close": [1.0]*k, "Volume": [1]*k}
        with patch.object(history, "get_price_history",
                          return_value=pd.DataFrame(cols(120), index=index[:120])):
            history.load_history_for_symbol("AAPL")
        with patch.object(history, "get_price_history",
                          return_value=pd.DataFrame(cols(150), index=index)):
            assert history.load_history_for_symbol("AAPL") == 30


class TestIntraday:
    def frame(self, closes):
        index = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=len(closes), freq="5min")
        return pd.DataFrame({"Close": closes}, index=index)

    def test_loads_and_reads_back(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("100"))
        with patch.object(history, "get_intraday", return_value=self.frame([1.5, 2.5, 3.5])):
            assert history.load_intraday_for_symbol("AAPL") == 3
        series = history.get_intraday_series("AAPL")
        assert [close for _, close in series] == [Decimal("1.5"), Decimal("2.5"), Decimal("3.5")]

    def test_reloading_does_not_duplicate(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("100"))
        frame = self.frame([1.5, 2.5])
        with patch.object(history, "get_intraday", return_value=frame):
            history.load_intraday_for_symbol("AAPL")
            assert history.load_intraday_for_symbol("AAPL") == 0

    def test_gaps_in_the_feed_are_skipped(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("100"))
        with patch.object(history, "get_intraday",
                          return_value=self.frame([1.5, float("nan"), 3.5])):
            assert history.load_intraday_for_symbol("AAPL") == 2

    def test_timestamps_keep_their_timezone(self, db):
        stocks.upsert_stock("AAPL", "Apple", Decimal("100"))
        with patch.object(history, "get_intraday", return_value=self.frame([1.5])):
            history.load_intraday_for_symbol("AAPL")
        ts, _ = history.get_intraday_series("AAPL")[0]
        assert ts.tzinfo is not None

    def test_one_day_means_the_last_session_even_on_a_weekend(self, db):
        """Regression: 1D filtered to the last 24 wall-clock hours, so it was
        empty every weekend and holiday. Bars from a session three days ago
        must still be the 1D chart when nothing newer exists."""
        stocks.upsert_stock("AAPL", "Apple", Decimal("100"))
        friday = pd.Timestamp.now(tz="America/New_York").normalize() - pd.Timedelta(days=3)
        older = friday - pd.Timedelta(days=1)
        bars = pd.DataFrame(
            {"Close": [1.0, 2.0, 3.0, 4.0]},
            index=[older + pd.Timedelta(hours=10), older + pd.Timedelta(hours=11),
                   friday + pd.Timedelta(hours=10), friday + pd.Timedelta(hours=11)],
        )
        with patch.object(history, "get_intraday", return_value=bars):
            history.load_intraday_for_symbol("AAPL")

        one = [close for _, close in history.get_intraday_sessions("AAPL", 1)]
        two = [close for _, close in history.get_intraday_sessions("AAPL", 2)]
        assert one == [Decimal("3.0"), Decimal("4.0")], "1D should be the latest session"
        assert len(two) == 4

    def test_intraday_table_rejects_nan(self, db):
        import psycopg2
        stocks.upsert_stock("AAPL", "Apple", Decimal("100"))
        with pytest.raises(psycopg2.errors.CheckViolation):
            with cursor(commit=True) as cur:
                cur.execute(
                    "INSERT INTO price_intraday (symbol, ts, close) VALUES ('AAPL', now(), 'NaN')"
                )


def quote(price, name="Apple Inc."):
    return patch.object(market_data, "get_quote", return_value=(Decimal(str(price)), name))


class TestStockPage:
    def test_requires_signing_in(self, anon):
        assert anon.get("/stock/AAPL").status_code == 302

    def test_renders_a_chart_from_stored_prices(self, client):
        store_daily("AAPL", [100, 110, 120])
        with quote("120"):
            body = text(client.get("/stock/AAPL?range=1m"))
        assert "<svg" in body
        assert 'class="chart-line"' in body
        assert "Apple Inc." in body

    def test_all_time_page_stays_light(self, client):
        """Regression: 11,529 plotted points made this page ~367 KB."""
        store_daily("AAPL", [100 + (i % 40) for i in range(3_000)])
        with quote("120"):
            size = len(client.get("/stock/AAPL?range=all").get_data())
        assert size < 60_000, f"all-time page is {size} bytes"

    def test_one_day_stats_describe_one_session_not_all_history(self, client):
        """Regression: the 1D panel queried every stored day, so it showed a
        1980 split-adjusted $0.04 as the one-day low."""
        store_daily("AAPL", [0.04, 0.05, 330, 331])  # ancient cheap history
        base = pd.Timestamp.now(tz="America/New_York").normalize() - pd.Timedelta(days=1)
        bars = pd.DataFrame({"Close": [331.0, 335.0, 332.0]},
                            index=[base + pd.Timedelta(hours=h) for h in (10, 11, 12)])
        with patch.object(history, "get_intraday", return_value=bars):
            history.load_intraday_for_symbol("AAPL")
        with quote("332"):
            body = text(client.get("/stock/AAPL?range=1d"))
        stats = body[body.index("Over 1 day"):]
        assert "$0.04" not in stats
        assert "$331.00" in stats and "$335.00" in stats

    def test_description_reads_as_a_sentence(self, client):
        store_daily("AAPL", [100, 125])
        with quote("125"):
            body = text(client.get("/stock/AAPL?range=1m"))
        assert "a up of" not in body
        assert "a change of +25.00, up over the period" in body

    def test_symbol_is_upper_cased(self, client):
        store_daily("AAPL", [100, 110])
        with quote("110"):
            assert client.get("/stock/aapl").status_code == 200

    def test_every_range_link_is_offered(self, client):
        store_daily("AAPL", [100, 110])
        with quote("110"):
            body = text(client.get("/stock/AAPL"))
        for key in charts.RANGES:
            assert f"range={key}" in body

    def test_the_selected_range_is_marked(self, client):
        store_daily("AAPL", [100, 110])
        with quote("110"):
            body = text(client.get("/stock/AAPL?range=3m"))
        assert 'aria-current="true">3M' in body

    def test_chart_has_a_text_alternative(self, client):
        """A screen reader gets the same facts the line shows."""
        store_daily("AAPL", [100, 125])
        with quote("125"):
            body = text(client.get("/stock/AAPL?range=1m"))
        assert 'role="img"' in body
        assert "<title" in body and "<desc" in body
        assert "$100.00" in body and "$125.00" in body

    def test_a_rise_is_described_with_sign_and_word(self, client):
        store_daily("AAPL", [100, 125])
        with quote("125"):
            body = text(client.get("/stock/AAPL?range=1m"))
        assert "+25.00" in body
        assert ">up<" in body

    def test_empty_range_explains_and_offers_a_fetch(self, client):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        with quote("100"):
            body = text(client.get("/stock/AAPL?range=1d"))
        assert "No prices stored" in body
        assert 'action="/stock/AAPL/fetch"' in body

    def test_unknown_ticker_goes_back_to_buy_with_a_message(self, client):
        with patch.object(market_data, "get_quote", return_value=(None, "X")):
            response = client.get("/stock/NOPE", follow_redirects=True)
        assert "No market data found" in text(response)

    def test_shows_your_position_when_you_own_it(self, client, user):
        store_daily("AAPL", [100, 110])
        portfolio.record_purchase(user, "AAPL", "Apple Inc.", Decimal("100"), Decimal("3"))
        with quote("110"):
            body = text(client.get("/stock/AAPL"))
        assert "You own 3" in body
        assert "Sell AAPL" in body

    def test_no_sell_button_when_you_do_not_own_it(self, client):
        store_daily("AAPL", [100, 110])
        with quote("110"):
            body = text(client.get("/stock/AAPL"))
        assert "do not own any yet" in body
        assert "Sell AAPL" not in body

    def test_a_bad_range_value_falls_back_rather_than_erroring(self, client):
        store_daily("AAPL", [100, 110])
        with quote("110"):
            assert client.get("/stock/AAPL?range=nonsense").status_code == 200

    def test_the_chart_page_does_not_load_three_js(self, client):
        """WebGL stays on the landing page; charts are plain SVG."""
        store_daily("AAPL", [100, 110])
        with quote("110"):
            assert "three" not in text(client.get("/stock/AAPL")).lower()


class TestFetch:
    @pytest.fixture(autouse=True)
    def priced(self):
        """A fetch prices the stock before downloading anything. Answer
        that lookup here; these tests used to reach the real Yahoo."""
        with quote("100"):
            yield

    def test_daily_fetch_backfills_everything(self, client):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        with patch.object(history, "load_history_for_symbol", return_value=250) as load:
            response = client.post("/stock/AAPL/fetch", data={"range": "1y"},
                                   follow_redirects=True)
        load.assert_called_once_with("AAPL", period="max")
        assert "Fetched 250 new days" in text(response)

    @pytest.mark.parametrize("window,period,interval", [
        ("1d", "1d", "5m"),
        ("5d", "5d", "30m"),
    ])
    def test_intraday_fetch_uses_an_interval_yahoo_can_serve(
            self, client, window, period, interval):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        with patch.object(history, "load_intraday_for_symbol", return_value=12) as load:
            client.post("/stock/AAPL/fetch", data={"range": window})
        load.assert_called_once_with("AAPL", period, interval)

    def test_a_failed_fetch_is_reported_not_crashed(self, client, caplog):
        """Reported in words a person can act on. The exception's own text
        is logged, never shown."""
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        with patch.object(history, "load_history_for_symbol",
                          side_effect=RuntimeError("rate limited")):
            response = client.post("/stock/AAPL/fetch", data={"range": "1y"},
                                   follow_redirects=True)
        body = text(response)
        assert "Could not fetch prices for AAPL." in body
        assert "rate limited" not in body
        assert "rate limited" in caplog.text

    def test_fetching_a_stock_you_have_never_bought_works(self, client):
        """Regression: price_history references stocks(symbol), and a company
        you have only viewed has no stocks row. The other fetch tests mock the
        loader, so this one runs the real insert against a mocked Yahoo frame
        with NO stocks row seeded beforehand."""
        frame = pd.DataFrame(
            {"Open": [1.0, 1.1], "High": [1.2, 1.3], "Low": [0.9, 1.0],
             "Close": [1.1, 1.2], "Volume": [100, 200]},
            index=pd.to_datetime([date.today() - timedelta(days=1), date.today()]),
        )
        with quote("1.20", "Brand New Co"),                 patch.object(history, "get_price_history", return_value=frame):
            response = client.post("/stock/NEWCO/fetch", data={"range": "1y"},
                                   follow_redirects=True)

        body = text(response)
        assert "Could not fetch" not in body
        assert "Fetched 2 new days" in body
        assert len(history.get_series("NEWCO")) == 2

    def test_fetch_requires_signing_in(self, anon):
        assert anon.post("/stock/AAPL/fetch", data={"range": "1y"}).status_code == 302


class TestTablesLinkToStockPages:
    @pytest.mark.parametrize("path", ["/", "/sell", "/balance", "/history"])
    def test_tickers_link_through(self, client, user, path):
        portfolio.record_purchase(user, "AAPL", "Apple Inc.", Decimal("100"), Decimal("2"))
        assert 'href="/stock/AAPL"' in text(client.get(path))

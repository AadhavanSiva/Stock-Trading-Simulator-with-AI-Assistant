"""Chart axes, and whether a long range admits when history is incomplete."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest

from portfolio_tracker import charts
from portfolio_tracker.db import cursor
from portfolio_tracker.models import history, stocks
from portfolio_tracker.services import market_data


def text(response):
    return response.get_data(as_text=True)


def store_daily(symbol, closes, start):
    stocks.upsert_stock(symbol, f"{symbol} Inc.", Decimal(str(closes[-1])))
    with cursor(commit=True) as cur:
        for offset, close in enumerate(closes):
            cur.execute(
                "INSERT INTO price_history (symbol, date, close) VALUES (%s, %s, %s)",
                (symbol, start + timedelta(days=offset), Decimal(str(close))),
            )


def quote(price="120", name="Apple Inc."):
    return patch.object(market_data, "get_quote", return_value=(Decimal(price), name))


class TestPriceAxis:
    def labels(self, low, high):
        start, end, ticks = charts.price_axis(Decimal(low), Decimal(high))
        step = ticks[1] - ticks[0]
        return [charts.price_label(t, step) for t in ticks]

    def test_ticks_bracket_the_data(self):
        start, end, ticks = charts.price_axis(Decimal("305.59"), Decimal("339.79"))
        assert start <= Decimal("305.59") and end >= Decimal("339.79")
        assert ticks[0] == start and ticks[-1] == end

    def test_whole_dollar_steps_use_whole_dollar_labels(self):
        assert self.labels("305.59", "339.79") == ["$300", "$310", "$320", "$330", "$340"]

    def test_thousands_are_separated(self):
        assert "$1,000" in self.labels("850", "1210")

    def test_sub_dollar_stocks_get_cents(self):
        assert self.labels("0.012", "0.047") == ["$0.01", "$0.02", "$0.03", "$0.04", "$0.05"]

    def test_quarter_steps_show_two_decimals_not_one(self):
        """A $2.50 step labelled "$12.5" reads as a typo."""
        labels = self.labels("10", "20")
        assert "$12.50" in labels and "$12.5" not in labels

    def test_never_goes_below_zero(self):
        start, _, _ = charts.price_axis(Decimal("0.04"), Decimal("339.79"))
        assert start == Decimal(0)

    def test_flat_series_still_gets_a_scale(self):
        start, end, ticks = charts.price_axis(Decimal("50"), Decimal("50"))
        assert start < Decimal("50") < end
        assert len(ticks) >= 3

    def test_ticks_are_exact_decimals(self):
        """Float steps drift: 0.1 + 0.2 must not become $0.30000000000000004."""
        _, _, ticks = charts.price_axis(Decimal("0.1"), Decimal("0.5"))
        assert all(isinstance(t, Decimal) for t in ticks)
        assert Decimal("0.3") in ticks

    def test_gridlines_sit_exactly_on_their_labels(self):
        chart = charts.build([("a", 305), ("b", 339)], height=220)
        for tick in chart.y_ticks:
            assert tick["pct"] == pytest.approx(tick["y"] / 220 * 100, abs=0.01)

    def test_top_label_is_the_highest_price(self):
        chart = charts.build([("a", 305), ("b", 339)])
        values = [t["value"] for t in chart.y_ticks]
        assert values == sorted(values, reverse=True)

    def test_every_point_stays_inside_the_plot(self):
        chart = charts.build([("a", 0.04), ("b", 339.79), ("c", 120)], height=220)
        assert all(0 <= p["y"] <= 220 for p in chart.points)


class TestDateAxis:
    def ticks(self, series, window):
        return [t["label"] for t in charts.build(series, window=window).x_ticks]

    def days(self, start, n):
        return [(start + timedelta(days=i), 100 + i) for i in range(n)]

    def test_short_ranges_show_month_and_day(self):
        labels = self.ticks(self.days(date(2026, 3, 12), 180), "6m")
        assert labels[0] == "Mar 12"

    def test_day_numbers_are_not_zero_padded(self):
        """strftime's %-d is not portable (it fails on Windows)."""
        assert self.ticks(self.days(date(2026, 3, 2), 30), "1m")[0] == "Mar 2"

    def test_one_year_shows_month_and_year(self):
        assert self.ticks(self.days(date(2025, 9, 15), 365), "1y")[0] == "Sep 2025"

    def test_all_time_shows_years(self):
        labels = self.ticks(self.days(date(1980, 12, 12), 16_700), "all")
        assert labels[0] == "1980" and labels[-1] == "2026"

    def test_one_day_shows_exchange_time_not_utc(self):
        """13:30 UTC is the 9:30am New York open."""
        bars = [(datetime(2026, 9, 11, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=5 * i), 330)
                for i in range(78)]
        labels = self.ticks(bars, "1d")
        assert labels[0] == "9:30am"
        assert labels[-1] == "3:55pm"

    def test_a_short_all_time_history_is_labelled_by_month(self):
        """Regression: six months under ALL collapsed the axis to one "2026"."""
        labels = self.ticks(self.days(date(2026, 3, 12), 180), "all")
        assert len(labels) >= 4
        assert labels[0] == "Mar 2026"

    def test_repeated_labels_are_dropped(self):
        """Five ticks across a few sessions can land twice on one day."""
        bars = [(datetime(2026, 9, 8, 14, tzinfo=timezone.utc) + timedelta(minutes=30 * i), 100)
                for i in range(20)]
        labels = self.ticks(bars, "5d")
        assert len(labels) == len(set(labels))

    def test_outer_labels_hang_inward(self):
        ticks = charts.build(self.days(date(2026, 1, 1), 60), window="3m").x_ticks
        assert ticks[0]["edge"] == "start"
        assert ticks[-1]["edge"] == "end"

    def test_a_non_date_label_does_not_break_the_chart(self):
        """A bad label should cost an axis caption, never the whole chart."""
        chart = charts.build([("first", 1), (object(), 2)], window="1m")
        assert chart is not None and len(chart.x_ticks) == 2


class TestAxesOnThePage:
    @pytest.fixture
    def page(self, client):
        store_daily("AAPL", [100 + i for i in range(40)], date.today() - timedelta(days=39))
        stocks.mark_full_history_loaded("AAPL")
        with quote():
            return text(client.get("/stock/AAPL?range=1m"))

    def test_price_axis_is_labelled_in_dollars(self, page):
        assert "Price (USD)" in page
        assert 'class="y-tick num"' in page
        assert ">$110<" in page or ">$120<" in page

    def test_date_axis_is_labelled(self, page):
        assert ">Date<" in page.replace("\n", "").replace("  ", "")
        assert 'class="x-tick' in page

    def test_axis_labels_are_hidden_from_screen_readers(self, page):
        """The SVG description already states the same facts in words."""
        assert '<div class="chart-y" aria-hidden="true">' in page
        assert '<div class="chart-x" aria-hidden="true">' in page

    def test_gridlines_are_drawn(self, page):
        assert page.count('class="chart-grid"') >= 3

    def test_intraday_axis_says_it_is_eastern_time(self, client):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
        start = pd.Timestamp.now(tz="America/New_York").normalize() - pd.Timedelta(days=1)
        bars = pd.DataFrame({"Close": [1.0, 2.0, 3.0]},
                            index=[start + pd.Timedelta(hours=h) for h in (10, 11, 12)])
        with patch.object(history, "get_intraday", return_value=bars):
            history.load_intraday_for_symbol("AAPL")
        with quote():
            assert "Time (US Eastern)" in text(client.get("/stock/AAPL?range=1d"))


class TestAllTimeIsHonest:
    """Regression: six months stored drew under an "All time" label with no
    way to load more, because the only check was 'fewer than 2 points'."""

    def six_months(self):
        store_daily("AAPL", [300 + (i % 20) for i in range(180)],
                    date.today() - timedelta(days=179))

    def test_all_flags_a_partial_history(self, client):
        self.six_months()
        with quote():
            body = text(client.get("/stock/AAPL?range=all"))
        assert "isn't the full history yet" in body
        assert "Load full history" in body
        assert "All time · partial" in body

    def test_the_headline_does_not_claim_all_time(self, client):
        self.six_months()
        with quote():
            body = text(client.get("/stock/AAPL?range=all"))
        since = (date.today() - timedelta(days=179))
        assert f"since {since:%b} {since.day}, {since.year}" in body
        assert "Over all time" not in body

    def test_one_year_with_six_months_stored_is_partial(self, client):
        self.six_months()
        with quote():
            body = text(client.get("/stock/AAPL?range=1y"))
        assert "Part of this range is missing" in body

    def test_a_range_that_is_fully_covered_is_not_flagged(self, client):
        self.six_months()
        with quote():
            body = text(client.get("/stock/AAPL?range=3m"))
        assert "Part of this range is missing" not in body
        assert "Load full history" not in body

    def test_once_full_history_is_loaded_the_notice_goes(self, client):
        """A company listed three months ago should not nag forever."""
        self.six_months()
        stocks.mark_full_history_loaded("AAPL")
        with quote():
            body = text(client.get("/stock/AAPL?range=all"))
        assert "isn't the full history yet" not in body
        assert "Over all time" in body

    def test_fetching_from_all_loads_everything_and_marks_it(self, client):
        self.six_months()
        n = 400
        frame = pd.DataFrame(
            {"Open": [1.0] * n, "High": [1.0] * n, "Low": [1.0] * n,
             "Close": [1.0] * n, "Volume": [1] * n},
            index=pd.date_range(end=date.today() - timedelta(days=200), periods=n, freq="D"),
        )
        with quote(), patch.object(history, "get_price_history", return_value=frame) as fetch:
            client.post("/stock/AAPL/fetch", data={"range": "all"})
            body = text(client.get("/stock/AAPL?range=all"))

        assert fetch.call_args.kwargs.get("period") == "max"
        assert stocks.full_history_loaded("AAPL")
        assert "isn't the full history yet" not in body

    def test_a_failed_fetch_leaves_history_marked_partial(self, client):
        self.six_months()
        with quote(), patch.object(history, "get_price_history",
                                   side_effect=RuntimeError("rate limited")):
            client.post("/stock/AAPL/fetch", data={"range": "all"})
        assert not stocks.full_history_loaded("AAPL")

    def test_stats_heading_matches_what_is_stored(self, client):
        self.six_months()
        with quote():
            body = text(client.get("/stock/AAPL?range=all"))
        assert "<h2>Since " in body


class TestReadouts:
    """Hover, keyboard and table readouts: exact dates and prices per point."""

    def test_daily_points_read_as_full_dates_and_dollars(self):
        series = [(date(2026, 1, 2), Decimal("100")), (date(2026, 1, 5), Decimal("1234.5"))]
        points = charts.readouts(charts.build(series))
        assert points[0][2] == "Jan 2, 2026" and points[1][3] == "$1,234.50"
        assert points[0][0] == 0 and points[-1][0] == 100

    def test_intraday_points_are_labelled_in_eastern_time(self):
        from datetime import datetime, timezone
        moment = datetime(2026, 9, 11, 14, 35, tzinfo=timezone.utc)   # 10:35 ET
        assert charts.point_label(moment) == "Fri Sep 11, 10:35am ET"

    def test_long_series_are_capped_and_keep_the_latest_price(self):
        series = [(date(2000, 1, 1) + timedelta(days=i), Decimal(100 + i % 7)) for i in range(5000)]
        chart = charts.build(series)
        points = charts.readouts(chart)
        assert len(points) <= charts.MAX_READOUTS
        assert points[-1][1] == round(chart.points[-1]["y"] / chart.height * 100, 2)

    def test_no_chart_means_no_readouts(self):
        assert charts.readouts(None) == []

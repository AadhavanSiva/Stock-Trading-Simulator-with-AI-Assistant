"""What an account was worth, over time — and the return computed from it."""
from decimal import Decimal
from unittest.mock import patch

import pytest

from portfolio_tracker import operations
from portfolio_tracker.db import cursor
from portfolio_tracker.models import portfolio, stocks, users, value_history


@pytest.fixture
def seeded(user):
    stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
    stocks.upsert_stock("MSFT", "Microsoft Corp.", Decimal("200"))
    return user


def sample_at(user_id, offset_days, total):
    """Write a sample dated in the past, for series tests."""
    with cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO portfolio_value_history
                (user_id, recorded_at, cash, holdings_value, total_value)
            VALUES (%s, now() - make_interval(days => %s), %s, 0, %s)
            """,
            (user_id, offset_days, total, total),
        )


class TestSampling:
    def test_a_sample_records_the_three_figures(self, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        operations.record_value_sample(seeded)

        recorded_at, cash, holdings, total = value_history.latest(seeded)
        assert cash == Decimal("49000.00")
        assert holdings == Decimal("1000")
        assert total == Decimal("50000.00")
        assert recorded_at is not None

    def test_the_total_is_cash_plus_holdings(self, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        stocks.update_stock_price("AAPL", Decimal("150"))
        operations.record_value_sample(seeded)

        _, cash, holdings, total = value_history.latest(seeded)
        assert total == cash + holdings

    def test_an_empty_account_still_samples(self, seeded):
        """A flat line at the opening balance is a real answer."""
        operations.record_value_sample(seeded)
        _, cash, holdings, total = value_history.latest(seeded)
        assert holdings == 0
        assert total == cash == users.starting_cash()

    def test_an_unpriced_holding_is_left_out_rather_than_counted_as_zero(self, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        with cursor(commit=True) as cur:
            cur.execute("UPDATE stocks SET current_price = NULL WHERE symbol = 'AAPL'")
        operations.record_value_sample(seeded)

        _, cash, holdings, _ = value_history.latest(seeded)
        assert holdings == 0 and cash == Decimal("49000.00")

    def test_samples_are_scoped_to_their_account(self, seeded, other_user):
        operations.record_value_sample(seeded)
        assert value_history.count(seeded) == 1
        assert value_history.count(other_user) == 0


class TestRefreshTakesASample:
    def test_refreshing_prices_records_one(self, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        before = value_history.count(seeded)

        with patch("portfolio_tracker.services.market_data.get_quote",
                   return_value=(Decimal("120"), "Apple Inc.", Decimal("118"))):
            operations.refresh_prices(seeded)

        assert value_history.count(seeded) == before + 1

    def test_the_sample_reflects_the_refreshed_price(self, seeded):
        """Taken after the prices move, not before."""
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        with patch("portfolio_tracker.services.market_data.get_quote",
                   return_value=(Decimal("120"), "Apple Inc.", Decimal("118"))):
            operations.refresh_prices(seeded)

        _, _, holdings, _ = value_history.latest(seeded)
        assert holdings == Decimal("1200")

    def test_a_failed_refresh_still_records_one(self, seeded):
        """A gap would read as "no change"; a flat stretch is the truth."""
        from portfolio_tracker.errors import MarketDataUnavailable

        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        before = value_history.count(seeded)

        with patch("portfolio_tracker.services.market_data.get_quote",
                   side_effect=MarketDataUnavailable("AAPL")):
            report = operations.refresh_prices(seeded)

        assert report.failed == 1
        assert value_history.count(seeded) == before + 1


class TestPercentReturn:
    def test_an_untouched_account_has_returned_nothing(self, seeded):
        assert operations.percent_return(seeded) == 0

    def test_a_gain_shows_as_a_positive_percentage(self, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("100"))   # $10,000
        stocks.update_stock_price("AAPL", Decimal("150"))           # now $15,000
        # 50,000 - 10,000 cash + 15,000 holdings = 55,000, so +10%.
        assert operations.percent_return(seeded) == 10

    def test_a_loss_shows_as_a_negative_percentage(self, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("100"))
        stocks.update_stock_price("AAPL", Decimal("50"))
        assert operations.percent_return(seeded) == -10

    def test_it_is_measured_from_the_opening_balance(self, seeded):
        """Not from the first sample: nothing is ever paid in or out, so
        the opening balance is what the account is answerable for."""
        sample_at(seeded, 30, Decimal("10000"))    # a much lower early reading
        assert operations.percent_return(seeded) == 0

    def test_buying_alone_does_not_move_it(self, seeded):
        """Cash becomes shares of equal value; nothing has been gained."""
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("50"))
        assert operations.percent_return(seeded) == 0


class TestThePerformanceChart:
    def test_nothing_to_draw_from_a_single_sample(self, seeded):
        """One point is a dot, not a line."""
        operations.record_value_sample(seeded)
        assert operations.performance(seeded) is None

    def test_nothing_to_draw_from_no_samples(self, seeded):
        assert operations.performance(seeded) is None

    def test_two_samples_make_a_chart(self, seeded):
        sample_at(seeded, 2, Decimal("50000"))
        sample_at(seeded, 1, Decimal("52000"))
        result = operations.performance(seeded)
        assert result is not None
        assert result.samples == 2
        assert result.chart.path

    def test_it_reports_the_change_across_the_window(self, seeded):
        sample_at(seeded, 2, Decimal("50000"))
        sample_at(seeded, 1, Decimal("52500"))
        result = operations.performance(seeded)
        assert result.start_value == Decimal("50000")
        assert result.end_value == Decimal("52500")
        assert result.change == Decimal("2500")
        assert result.percent == 5

    def test_a_window_excludes_older_samples(self, seeded):
        sample_at(seeded, 90, Decimal("10000"))
        sample_at(seeded, 2, Decimal("50000"))
        sample_at(seeded, 1, Decimal("52000"))
        windowed = operations.performance(seeded, days=30)
        assert windowed.samples == 2
        assert windowed.start_value == Decimal("50000")

    def test_samples_are_plotted_oldest_first(self, seeded):
        sample_at(seeded, 3, Decimal("10000"))
        sample_at(seeded, 1, Decimal("20000"))
        result = operations.performance(seeded)
        assert result.start_value < result.end_value

    def test_one_account_cannot_see_another_s_curve(self, seeded, other_user):
        sample_at(other_user, 2, Decimal("90000"))
        sample_at(other_user, 1, Decimal("95000"))
        assert operations.performance(seeded) is None


class TestThePortfolioPage:
    def test_it_shows_the_return(self, client, seeded):
        """The KPI row only renders once something is held."""
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        page = client.get("/").data
        assert b"Return" in page
        assert b"you started with" in page

    def test_it_shows_the_chart_once_there_is_one(self, client, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        sample_at(seeded, 2, Decimal("50000"))
        sample_at(seeded, 1, Decimal("52000"))
        page = client.get("/").data
        assert b"Value over time" in page
        assert b"chart-line" in page

    def test_it_omits_the_chart_when_there_is_nothing_to_plot(self, client, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        assert b"Value over time" not in client.get("/").data

    def test_the_chart_is_described_for_screen_readers(self, client, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        sample_at(seeded, 2, Decimal("50000"))
        sample_at(seeded, 1, Decimal("52000"))
        page = client.get("/").data
        assert b"Account value over time" in page
        assert b'id="perf-desc"' in page


class TestDeletion:
    def test_deleting_an_account_removes_its_samples(self, seeded):
        operations.record_value_sample(seeded)
        assert value_history.count(seeded) == 1
        operations.delete_account(seeded, "DELETE")
        assert value_history.count(seeded) == 0


class TestTheExport:
    def test_it_reports_the_return(self, seeded):
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("100"))
        stocks.update_stock_price("AAPL", Decimal("150"))
        data = operations.export_account(seeded)
        assert Decimal(data["totals"]["percent_return"]) == 10

"""Report rendering tests — these are the paths that used to crash."""
from decimal import Decimal

from portfolio_tracker import reports
from portfolio_tracker.db import cursor
from portfolio_tracker.models import portfolio, stocks


def buy(user_id, symbol, shares, price, name="Test Co"):
    return portfolio.record_purchase(
        user_id, symbol, name, Decimal(str(price)), Decimal(str(shares))
    )


class TestShareFormatting:
    def test_trailing_zeros_are_trimmed(self):
        assert reports._shares(Decimal("10.0000000")) == "10"

    def test_fractional_shares_survive(self):
        assert reports._shares(Decimal("2.3242443")) == "2.3242443"

    def test_round_numbers_do_not_go_scientific(self):
        """Decimal.normalize() renders 100 as 1E+2 without the quantize step."""
        assert reports._shares(Decimal("100")) == "100"


class TestPortfolioSummary:
    def test_empty_portfolio_says_so_and_shows_cash(self, user, capsys):
        reports.print_portfolio_summary(user)
        out = capsys.readouterr().out
        assert "No holdings yet" in out
        assert "$50,000.00" in out

    def test_unpriced_holding_does_not_crash_the_report(self, user, capsys):
        """Regression: shares * None raised TypeError and killed the whole report."""
        buy(user, "NEWCO", 3, 50, name="New Co")
        with cursor(commit=True) as cur:
            cur.execute("UPDATE stocks SET current_price = NULL WHERE symbol = 'NEWCO'")

        reports.print_portfolio_summary(user)

        out = capsys.readouterr().out
        assert "NEWCO" in out
        assert "no price yet" in out

    def test_totals_add_up(self, seeded, capsys):
        buy(seeded, "AAPL", 10, 100)
        buy(seeded, "MSFT", 2, 300)
        stocks.update_stock_price("AAPL", Decimal("200"))
        stocks.update_stock_price("MSFT", Decimal("400"))

        reports.print_portfolio_summary(seeded)

        out = capsys.readouterr().out
        assert "2,800.00" in out   # market value 10*200 + 2*400
        assert "1,600.00" in out   # cost basis  10*100 + 2*300
        assert "1,200.00" in out   # gain

    def test_shows_cash_and_account_total(self, seeded, capsys):
        buy(seeded, "AAPL", 10, 100)
        reports.print_portfolio_summary(seeded)
        out = capsys.readouterr().out
        assert "Cash available" in out
        assert "$49,000.00" in out
        assert "Total account value" in out


class TestBalanceReport:
    def test_all_cash_before_any_purchase(self, user, capsys):
        reports.print_balance(user)
        out = capsys.readouterr().out
        assert "Cash available" in out
        assert "$50,000.00" in out

    def test_cash_plus_investments_equals_total(self, seeded, capsys):
        buy(seeded, "AAPL", 10, 100)
        stocks.update_stock_price("AAPL", Decimal("150"))

        reports.print_balance(seeded)

        out = capsys.readouterr().out
        assert "$49,000.00" in out   # cash after spending 1,000
        assert "$1,500.00" in out    # holdings now worth 10 * 150
        assert "$50,500.00" in out   # total


class TestHistorySummary:
    def test_no_history_says_so(self, db, capsys):
        reports.print_history_summary()
        assert "No historical data yet" in capsys.readouterr().out

    def test_full_report_runs_end_to_end(self, seeded, capsys):
        buy(seeded, "AAPL", 1, 100)
        reports.print_full_report(seeded)
        out = capsys.readouterr().out
        assert "PORTFOLIO SUMMARY REPORT" in out

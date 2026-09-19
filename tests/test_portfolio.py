"""Portfolio model tests: ownership, weighted-average cost, and cash."""
from decimal import Decimal

import pytest

from portfolio_tracker.db import cursor
from portfolio_tracker.errors import InsufficientFunds
from portfolio_tracker.models import portfolio, stocks, users

STARTING = Decimal("50000")


def buy(user_id, symbol, shares, price, name="Test Co"):
    return portfolio.record_purchase(
        user_id, symbol, name, Decimal(str(price)), Decimal(str(shares))
    )


def held(user_id, symbol):
    with cursor() as cur:
        cur.execute(
            "SELECT shares, purchase_price FROM portfolio WHERE user_id = %s AND symbol = %s",
            (user_id, symbol),
        )
        return cur.fetchall()


class TestPurchases:
    def test_first_buy_creates_the_position(self, seeded):
        shares, cost, cash = buy(seeded, "AAPL", 10, 100)
        assert shares == Decimal("10")
        assert cost == Decimal("100")

    def test_repeat_buy_merges_at_weighted_average_cost(self, seeded):
        buy(seeded, "AAPL", 10, 100)
        shares, cost, _ = buy(seeded, "AAPL", 5, 200)

        # (10*100 + 5*200) / 15 == 133.33...
        assert shares == Decimal("15")
        assert cost == pytest.approx(Decimal("133.3333"), abs=Decimal("0.0001"))

    def test_repeat_buy_never_opens_a_second_row(self, seeded):
        buy(seeded, "AAPL", 10, 100)
        buy(seeded, "AAPL", 5, 200)
        assert len(held(seeded, "AAPL")) == 1

    def test_decimal_cost_basis_is_exact(self, seeded):
        """The whole point of Decimal: 0.1 + 0.2 must be 0.3."""
        buy(seeded, "AAPL", "0.1", 10)
        buy(seeded, "AAPL", "0.2", 10)
        shares, cost = portfolio.get_holding(seeded, "AAPL")
        assert shares == Decimal("0.3")
        assert cost == Decimal("10")


class TestCash:
    def test_new_account_starts_with_the_standard_balance(self, user):
        assert users.get_cash(user) == STARTING

    def test_buying_debits_cash(self, seeded):
        _, _, cash = buy(seeded, "AAPL", 10, 100)
        assert cash == STARTING - Decimal("1000")
        assert users.get_cash(seeded) == STARTING - Decimal("1000")

    def test_selling_credits_cash(self, seeded):
        buy(seeded, "AAPL", 10, 100)
        sale = portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("4"))
        assert sale.sold == Decimal("4")
        assert sale.cash == STARTING - Decimal("1000") + Decimal("600")

    def test_round_trip_at_the_same_price_returns_the_balance(self, seeded):
        buy(seeded, "AAPL", 10, 100)
        portfolio.record_sale(seeded, "AAPL", Decimal("100"), Decimal("10"))
        assert users.get_cash(seeded) == STARTING

    def test_cannot_spend_more_than_the_balance(self, seeded):
        with pytest.raises(InsufficientFunds):
            buy(seeded, "AAPL", 1000, 100)  # $100,000 against $50,000

    def test_a_refused_purchase_changes_nothing(self, seeded):
        with pytest.raises(InsufficientFunds):
            buy(seeded, "AAPL", 1000, 100)
        assert users.get_cash(seeded) == STARTING
        assert portfolio.get_holding(seeded, "AAPL") is None

    def test_spending_the_exact_balance_is_allowed(self, seeded):
        _, _, cash = buy(seeded, "AAPL", 500, 100)  # exactly $50,000
        assert cash == Decimal("0")

    def test_cash_settles_at_whole_cents(self, seeded):
        """Fractional shares produce long products; money stops at 2 places."""
        _, _, cash = buy(seeded, "AAPL", "0.3333", "100.005")
        spent = STARTING - cash
        assert spent == Decimal("33.33")  # 0.3333 * 100.005 = 33.3316..., to cents
        assert spent.as_tuple().exponent == -2

    def test_balance_cannot_go_negative_at_the_database_level(self, seeded):
        import psycopg2

        with pytest.raises(psycopg2.errors.CheckViolation):
            with cursor(commit=True) as cur:
                cur.execute("UPDATE users SET cash = -1 WHERE id = %s", (seeded,))


class TestSales:
    def test_partial_sale_leaves_the_remainder(self, seeded):
        buy(seeded, "AAPL", 10, 100)
        sale = portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("4"))
        assert sale.sold == Decimal("4")
        assert sale.remaining == Decimal("6")

    def test_full_sale_closes_the_position(self, seeded):
        buy(seeded, "AAPL", 10, 100)
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("10"))
        assert portfolio.get_holding(seeded, "AAPL") is None

    def test_selling_a_merged_position_keeps_the_rest(self, seeded):
        """Regression: selling the first lot used to delete the whole symbol."""
        buy(seeded, "AAPL", 10, 100)
        buy(seeded, "AAPL", 5, 200)

        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("10"))

        remaining = portfolio.get_holding(seeded, "AAPL")
        assert remaining is not None, "the rest of the position was destroyed"
        assert remaining[0] == Decimal("5")

    def test_cannot_oversell(self, seeded):
        buy(seeded, "AAPL", 10, 100)
        sold = portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("11")).sold
        assert sold == 0
        assert portfolio.get_holding(seeded, "AAPL")[0] == Decimal("10")

    def test_a_refused_sale_does_not_pay_out(self, seeded):
        buy(seeded, "AAPL", 10, 100)
        before = users.get_cash(seeded)
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("11"))
        assert users.get_cash(seeded) == before

    def test_unknown_symbol_is_a_no_op(self, seeded):
        assert portfolio.record_sale(seeded, "NOPE", Decimal("1"), Decimal("1"))[0] == 0

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-5", "0"])
    def test_rejects_nonsense_quantities(self, seeded, bad):
        buy(seeded, "AAPL", 10, 100)
        assert portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal(bad))[0] == 0
        assert portfolio.get_holding(seeded, "AAPL")[0] == Decimal("10")


class TestIsolationBetweenAccounts:
    """The reason UNIQUE must be (user_id, symbol) and every query filtered."""

    def test_two_accounts_can_hold_the_same_ticker(self, seeded, other_user):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("200"))
        buy(seeded, "AAPL", 10, 100)
        buy(other_user, "AAPL", 3, 150)

        assert portfolio.get_holding(seeded, "AAPL")[0] == Decimal("10")
        assert portfolio.get_holding(other_user, "AAPL")[0] == Decimal("3")

    def test_one_account_cannot_see_another_holdings(self, seeded, other_user):
        buy(seeded, "AAPL", 10, 100)
        assert portfolio.get_portfolio_symbols(other_user) == []
        assert portfolio.get_holdings_with_details(other_user) == []

    def test_selling_only_touches_your_own_position(self, seeded, other_user):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("200"))
        buy(seeded, "AAPL", 10, 100)
        buy(other_user, "AAPL", 3, 150)

        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("10"))

        assert portfolio.get_holding(seeded, "AAPL") is None
        assert portfolio.get_holding(other_user, "AAPL")[0] == Decimal("3")

    def test_cash_is_per_account(self, seeded, other_user):
        buy(seeded, "AAPL", 10, 100)
        assert users.get_cash(seeded) == STARTING - Decimal("1000")
        assert users.get_cash(other_user) == STARTING


class TestSchemaConstraints:
    def test_same_user_same_symbol_twice_is_rejected(self, seeded):
        import psycopg2

        buy(seeded, "AAPL", 10, 100)
        with pytest.raises(psycopg2.errors.UniqueViolation):
            with cursor(commit=True) as cur:
                cur.execute(
                    "INSERT INTO portfolio (user_id, symbol, shares, purchase_price)"
                    " VALUES (%s, 'AAPL', 1, 1)",
                    (seeded,),
                )

    @pytest.mark.parametrize("shares", ["0", "-1", "NaN"])
    def test_invalid_share_counts_are_rejected(self, seeded, shares):
        """Postgres sorts NaN above every numeric, so 'shares > 0' alone lets it in."""
        import psycopg2

        with pytest.raises(psycopg2.errors.CheckViolation):
            with cursor(commit=True) as cur:
                cur.execute(
                    "INSERT INTO portfolio (user_id, symbol, shares, purchase_price)"
                    " VALUES (%s, 'AAPL', %s, 1)",
                    (seeded, Decimal(shares)),
                )

    def test_holdings_require_a_real_account(self, seeded):
        import psycopg2

        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            with cursor(commit=True) as cur:
                cur.execute(
                    "INSERT INTO portfolio (user_id, symbol, shares, purchase_price)"
                    " VALUES (99999, 'AAPL', 1, 1)"
                )

    def test_deleting_an_account_removes_its_holdings(self, seeded):
        buy(seeded, "AAPL", 10, 100)
        users.delete_account(seeded)
        assert portfolio.get_holding(seeded, "AAPL") is None

    def test_a_raw_delete_is_refused_and_says_where_to_go(self, seeded):
        """The cascade would reach the append-only trade log, so the plain
        DELETE is stopped — with a hint naming the sanctioned path, rather
        than leaving someone guessing at a constraint they cannot see."""
        buy(seeded, "AAPL", 10, 100)
        with pytest.raises(Exception) as caught:
            with cursor(commit=True) as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (seeded,))
        assert "delete_account" in str(caught.value)
        assert portfolio.get_holding(seeded, "AAPL") is not None


class TestHoldingsWithPrices:
    def test_unpriced_holding_still_appears(self, user):
        stocks.upsert_stock("NEWCO", "New Co", None)
        buy(user, "NEWCO", 3, 50)
        with cursor(commit=True) as cur:
            cur.execute("UPDATE stocks SET current_price = NULL WHERE symbol = 'NEWCO'")

        result = portfolio.get_holdings_with_details(user)
        assert len(result) == 1
        assert result[0][0] == "NEWCO"
        assert result[0][4] is None

    def test_gain_loss_is_computed(self, seeded):
        buy(seeded, "AAPL", 10, 100)
        stocks.update_stock_price("AAPL", Decimal("200"))
        symbol, name, shares, cost, current, gain = portfolio.get_holdings_with_details(seeded)[0]
        assert current == Decimal("200")
        assert gain == Decimal("1000")  # (200 - 100) * 10

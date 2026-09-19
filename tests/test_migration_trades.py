"""Migration 006, run against a database shaped the way a real one was
before the trade log existed.

The migration is the only code path that meets pre-existing data, and it
is the one nobody runs twice by accident — so it is worth proving it does
what it claims on a database that actually predates it.
"""
import os
from decimal import Decimal

import pytest

from portfolio_tracker.db import cursor

MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "migrations", "006_trades.sql"
)


@pytest.fixture
def before_the_migration(db):
    """A database with holdings but no trades table, as 005 left it."""
    with cursor(commit=True) as cur:
        cur.execute("DROP TABLE IF EXISTS trades CASCADE")
        cur.execute("DROP FUNCTION IF EXISTS trades_append_only() CASCADE")
        cur.execute(
            "INSERT INTO users (google_sub, email, display_name, cash) "
            "VALUES ('legacy-sub', 'legacy@example.com', 'Legacy', 40000) RETURNING id"
        )
        user_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO stocks (symbol, company_name, current_price) "
            "VALUES ('AAPL', 'Apple Inc.', 150), ('MSFT', 'Microsoft Corp.', 400)"
        )
        cur.execute(
            "INSERT INTO portfolio (user_id, symbol, shares, purchase_price) "
            "VALUES (%s, 'AAPL', 10, 100), (%s, 'MSFT', 5, 380)",
            (user_id, user_id),
        )
    return user_id


def run_migration():
    with open(MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    # The file manages its own BEGIN/COMMIT.
    with cursor() as cur:
        cur.connection.autocommit = True
        cur.execute(sql)
        cur.connection.autocommit = False


def trades_of(user_id):
    with cursor() as cur:
        cur.execute(
            "SELECT symbol, side, shares, price, total_value, cost_basis, backfilled "
            "FROM trades WHERE user_id = %s ORDER BY symbol",
            (user_id,),
        )
        return cur.fetchall()


class TestTheBackfill:
    def test_every_existing_holding_gets_an_opening_row(self, before_the_migration):
        run_migration()
        assert [row[0] for row in trades_of(before_the_migration)] == ["AAPL", "MSFT"]

    def test_the_opening_row_carries_the_position_s_own_numbers(self, before_the_migration):
        run_migration()
        aapl = trades_of(before_the_migration)[0]
        assert aapl[1] == "buy"
        assert aapl[2] == 10 and aapl[3] == 100
        assert aapl[4] == Decimal("1000.00")

    def test_it_is_marked_as_a_reconstruction(self, before_the_migration):
        """These are not trades that were observed, and must not claim to be."""
        run_migration()
        assert all(row[6] is True for row in trades_of(before_the_migration))

    def test_an_opening_row_realizes_nothing(self, before_the_migration):
        run_migration()
        assert all(row[5] is None for row in trades_of(before_the_migration))

    def test_it_is_dated_from_the_account_s_creation(self, before_the_migration):
        run_migration()
        with cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM trades t JOIN users u ON u.id = t.user_id "
                "WHERE t.user_id = %s AND t.traded_at = u.created_at",
                (before_the_migration,),
            )
            assert cur.fetchone()[0] == 2

    def test_an_account_with_no_holdings_gets_nothing(self, before_the_migration):
        with cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO users (google_sub, email) "
                "VALUES ('empty-sub', 'empty@example.com') RETURNING id"
            )
            empty = cur.fetchone()[0]
        run_migration()
        assert trades_of(empty) == []

    def test_the_log_reconciles_with_the_positions_it_was_built_from(
            self, before_the_migration):
        run_migration()
        with cursor() as cur:
            cur.execute(
                """
                SELECT p.symbol, p.shares, p.purchase_price, t.shares, t.price
                FROM portfolio p JOIN trades t
                  ON t.user_id = p.user_id AND t.symbol = p.symbol
                WHERE p.user_id = %s
                """,
                (before_the_migration,),
            )
            for _, held, paid, logged, price in cur.fetchall():
                assert held == logged and paid == price


class TestItIsSafeToReRun:
    def test_running_it_twice_does_not_double_the_rows(self, before_the_migration):
        run_migration()
        run_migration()
        assert len(trades_of(before_the_migration)) == 2

    def test_a_real_trade_made_afterwards_survives_a_re_run(self, before_the_migration):
        from portfolio_tracker.models import portfolio

        run_migration()
        portfolio.record_purchase(before_the_migration, "AAPL", "Apple Inc.",
                                  Decimal("150"), Decimal("2"))
        run_migration()
        rows = trades_of(before_the_migration)
        assert len(rows) == 3
        assert sum(1 for row in rows if row[6] is False) == 1

    def test_a_re_run_does_not_backfill_over_a_closed_position(self, before_the_migration):
        """Selling out leaves no holding, so there is nothing to re-open."""
        from portfolio_tracker.models import portfolio

        run_migration()
        portfolio.record_sale(before_the_migration, "MSFT", Decimal("400"), Decimal("5"))
        run_migration()
        msft = [row for row in trades_of(before_the_migration) if row[0] == "MSFT"]
        assert sum(1 for row in msft if row[6] is True) == 1


class TestTheGuardIsInstalled:
    def test_the_migration_leaves_the_log_append_only(self, before_the_migration):
        run_migration()
        with pytest.raises(Exception, match="append-only"):
            with cursor(commit=True) as cur:
                cur.execute("UPDATE trades SET price = 1 WHERE user_id = %s",
                            (before_the_migration,))

    def test_deleting_the_account_still_works_afterwards(self, before_the_migration):
        from portfolio_tracker.models import users

        run_migration()
        assert users.delete_account(before_the_migration) == "legacy@example.com"
        assert trades_of(before_the_migration) == []


@pytest.fixture(autouse=True)
def restore_schema(db):
    """Put the table back as schema.sql defines it, for the tests after these."""
    yield
    schema = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")
    with cursor(commit=True) as cur:
        cur.execute("DROP TABLE IF EXISTS trades CASCADE")
        cur.execute("DROP FUNCTION IF EXISTS trades_append_only() CASCADE")
        with open(schema, encoding="utf-8") as fh:
            cur.execute(fh.read())

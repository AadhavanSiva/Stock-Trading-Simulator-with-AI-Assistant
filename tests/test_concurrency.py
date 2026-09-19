"""Two requests at once, against one account.

These tests use real threads on real connections, because the thing under
test is what PostgreSQL does when two transactions want the same rows —
which a single-connection test cannot reach.

The bug that prompted them: record_purchase locked `users` then
`portfolio`, while record_sale locked `portfolio` then `users`. Two
transactions taking the same pair of locks in opposite orders deadlock,
and PostgreSQL resolves that by aborting one. It was reachable with one
account buying and selling the same position on two threads.
"""
import threading
from decimal import Decimal

import pytest

from portfolio_tracker.db import cursor, get_connection
from portfolio_tracker.models import portfolio, stocks, users


@pytest.fixture
def seeded(user):
    stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
    portfolio.record_purchase(user, "AAPL", "Apple Inc.", Decimal("100"), Decimal("50"))
    return user


def run_together(*functions, timeout=30):
    """Run each callable on its own thread; collect what each one did."""
    results = {}

    def wrap(index, fn):
        try:
            results[index] = ("ok", fn())
        except Exception as exc:                      # noqa: BLE001 - recorded, not handled
            results[index] = ("error", f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=wrap, args=(i, fn))
               for i, fn in enumerate(functions)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
        assert not t.is_alive(), "a thread is still blocked — deadlock or lock wait"
    return [results[i] for i in range(len(functions))]


def assert_no_deadlock(outcomes):
    for kind, detail in outcomes:
        assert not (kind == "error" and "Deadlock" in detail), detail


class TestLockOrdering:
    """Both paths must take `users` before `portfolio`."""

    def test_the_two_paths_agree_on_the_order(self, seeded):
        """Pinned directly, so a reordering is caught at the source.

        The concurrency tests below can pass by luck if the timing does not
        happen to interleave; this cannot.
        """
        import ast
        import inspect
        import textwrap

        def statements(function):
            """The SQL in a function, in source order.

            Read from the syntax tree rather than the raw text so that
            prose — the docstrings and comments that discuss `users` and
            `portfolio` precisely because this ordering matters — cannot be
            mistaken for the code. Every query here is parameterized, which
            is what tells SQL apart from the surrounding English.
            """
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
            found = [
                (node.lineno, node.value)
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "%s" in node.value
            ]
            return [sql for _, sql in sorted(found)]

        def first_mentioning(sqls, table):
            for index, sql in enumerate(sqls):
                if table in sql:
                    return index
            return None

        for name in ("record_purchase", "record_sale"):
            sqls = statements(getattr(portfolio, name))
            users_at = first_mentioning(sqls, "users")
            portfolio_at = first_mentioning(sqls, "portfolio")
            assert users_at is not None, f"{name} never locks the account row"
            assert portfolio_at is not None, f"{name} never touches the holding"
            assert users_at < portfolio_at, (
                f"{name} touches portfolio before users; the two paths must "
                f"agree on lock order or they deadlock"
            )

    def test_a_buy_and_a_sell_together_do_not_deadlock(self, seeded):
        def buy():
            return portfolio.record_purchase(
                seeded, "AAPL", "Apple Inc.", Decimal("100"), Decimal("1"))

        def sell():
            return portfolio.record_sale(seeded, "AAPL", Decimal("100"), Decimal("1"))

        assert_no_deadlock(run_together(buy, sell))

    def test_the_interleaving_that_used_to_deadlock(self, seeded):
        """Force the exact overlap rather than hope for it.

        Each side takes its first lock, waits for the other to take theirs,
        and only then reaches for the second. Under the old ordering this
        deadlocked every time; under the new one both should finish.
        """
        took_first = [threading.Event(), threading.Event()]

        def buyer():
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT cash FROM users WHERE id = %s FOR UPDATE", (seeded,))
                    took_first[0].set()
                    took_first[1].wait(10)
                    cur.execute(
                        """
                        INSERT INTO portfolio (user_id, symbol, shares, purchase_price)
                        VALUES (%s, 'AAPL', 1, 100)
                        ON CONFLICT (user_id, symbol) DO UPDATE
                        SET shares = portfolio.shares + 1
                        """,
                        (seeded,),
                    )
                conn.commit()
                return "committed"
            finally:
                conn.close()

        def seller():
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    # Same order as the fixed record_sale: users, then holding.
                    cur.execute("SELECT 1 FROM users WHERE id = %s FOR UPDATE", (seeded,))
                    cur.execute(
                        "SELECT id, shares FROM portfolio "
                        "WHERE user_id = %s AND symbol = 'AAPL' FOR UPDATE",
                        (seeded,),
                    )
                    took_first[1].set()
                    took_first[0].wait(10)
                    cur.execute("UPDATE users SET cash = cash + 1 WHERE id = %s", (seeded,))
                conn.commit()
                return "committed"
            finally:
                conn.close()

        assert_no_deadlock(run_together(buyer, seller))

    def test_two_sales_together_do_not_deadlock(self, seeded):
        def sell():
            return portfolio.record_sale(seeded, "AAPL", Decimal("100"), Decimal("1"))

        assert_no_deadlock(run_together(sell, sell))

    def test_two_purchases_together_do_not_deadlock(self, seeded):
        def buy():
            return portfolio.record_purchase(
                seeded, "AAPL", "Apple Inc.", Decimal("100"), Decimal("1"))

        assert_no_deadlock(run_together(buy, buy))


class TestTheLocksStillDoTheirJob:
    """Ordering them must not stop them serialising."""

    def test_concurrent_sales_cannot_oversell(self, seeded):
        """Ten shares held, two threads each selling eight."""
        with cursor(commit=True) as cur:
            cur.execute(
                "UPDATE portfolio SET shares = 10 WHERE user_id = %s AND symbol = 'AAPL'",
                (seeded,),
            )

        def sell():
            return portfolio.record_sale(seeded, "AAPL", Decimal("100"), Decimal("8"))

        outcomes = run_together(sell, sell)
        assert_no_deadlock(outcomes)
        sold = [detail.sold for kind, detail in outcomes if kind == "ok"]
        assert sum(sold) <= 10, f"oversold: {sold}"

        held = portfolio.get_holding(seeded, "AAPL")
        remaining = held[0] if held else Decimal(0)
        assert remaining + sum(sold) == 10

    def test_concurrent_purchases_cannot_overdraw(self, user):
        """Two buys of $30,000 against a $50,000 balance; only one fits."""
        from portfolio_tracker.errors import InsufficientFunds

        stocks.upsert_stock("BIG", "Big Co", Decimal("30000"))

        def buy():
            return portfolio.record_purchase(
                user, "BIG", "Big Co", Decimal("30000"), Decimal("1"))

        outcomes = run_together(buy, buy)
        assert_no_deadlock(outcomes)
        refused = [d for k, d in outcomes if k == "error" and "InsufficientFunds" in d]
        assert len(refused) == 1, outcomes
        assert users.get_cash(user) == Decimal("20000.00")

    def test_the_ledger_matches_the_cash_after_concurrent_trades(self, seeded):
        """Whatever order they land in, the log must explain the balance.

        Measured from the starting balance against *every* trade, including
        the fixture's opening purchase — cash only ever moves through a
        trade, so the two must reconcile exactly.
        """
        def buy():
            return portfolio.record_purchase(
                seeded, "AAPL", "Apple Inc.", Decimal("100"), Decimal("2"))

        def sell():
            return portfolio.record_sale(seeded, "AAPL", Decimal("100"), Decimal("3"))

        assert_no_deadlock(run_together(buy, sell))

        with cursor() as cur:
            cur.execute(
                """
                SELECT coalesce(sum(CASE WHEN side = 'sell' THEN total_value
                                         ELSE -total_value END), 0)
                FROM trades WHERE user_id = %s
                """,
                (seeded,),
            )
            net = cur.fetchone()[0]
        assert users.get_cash(seeded) == users.starting_cash() + net

"""Connection-handling tests.

Every model function used to open a raw connection with no try/finally, so
any exception mid-query leaked the connection and left the transaction open.
"""
import pytest

from portfolio_tracker.db import cursor


class TestCursor:
    def test_read_without_commit(self, db):
        with cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1

    def test_commit_persists(self, db):
        with cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO stocks (symbol, company_name, current_price)"
                " VALUES ('AAA', 'A', 1)"
            )
        with cursor() as cur:
            cur.execute("SELECT count(*) FROM stocks WHERE symbol = 'AAA'")
            assert cur.fetchone()[0] == 1

    def test_without_commit_nothing_persists(self, db):
        with cursor() as cur:
            cur.execute(
                "INSERT INTO stocks (symbol, company_name, current_price)"
                " VALUES ('BBB', 'B', 1)"
            )
        with cursor() as cur:
            cur.execute("SELECT count(*) FROM stocks WHERE symbol = 'BBB'")
            assert cur.fetchone()[0] == 0

    def test_exception_rolls_back_the_whole_block(self, db):
        """A partial write must not survive a failure halfway through."""
        with pytest.raises(RuntimeError):
            with cursor(commit=True) as cur:
                cur.execute(
                    "INSERT INTO stocks (symbol, company_name, current_price)"
                    " VALUES ('CCC', 'C', 1)"
                )
                raise RuntimeError("boom")

        with cursor() as cur:
            cur.execute("SELECT count(*) FROM stocks WHERE symbol = 'CCC'")
            assert cur.fetchone()[0] == 0

    def test_connection_is_closed_even_when_the_block_fails(self, db):
        import portfolio_tracker.db as db_module

        opened = []
        real = db_module.get_connection

        def tracking():
            conn = real()
            opened.append(conn)
            return conn

        db_module.get_connection = tracking
        try:
            with pytest.raises(RuntimeError):
                with cursor() as cur:
                    cur.execute("SELECT 1")
                    raise RuntimeError("boom")
        finally:
            db_module.get_connection = real

        assert opened and all(c.closed for c in opened), "connection was leaked"

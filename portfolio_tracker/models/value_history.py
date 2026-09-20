"""Samples of what an account was worth, over time.

Append-only in practice, though not enforced the way `trades` is: a trade
is a claim about something that happened and must never be restated, while
this is a measurement that can legitimately be re-taken. Nothing here
updates a sample either way.
"""
from decimal import Decimal

from portfolio_tracker.db import cursor


def record(user_id, cash, holdings_value, cur=None):
    """Store one sample. Returns its id.

    Accepts a cursor so a caller already inside a transaction can sample
    without opening a second one; on its own it manages its own.
    """
    cash = Decimal(cash)
    holdings_value = Decimal(holdings_value)
    total = cash + holdings_value

    def run(c):
        c.execute(
            """
            INSERT INTO portfolio_value_history
                (user_id, cash, holdings_value, total_value)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, cash, holdings_value, total),
        )
        return c.fetchone()[0]

    if cur is not None:
        return run(cur)
    with cursor(commit=True) as own:
        return run(own)


def series(user_id, since=None, limit=None):
    """Samples oldest first, for plotting.

    Ascending because a chart reads left to right; every other query in the
    app is newest-first, so this one says why it differs.
    """
    sql = """
        SELECT recorded_at, cash, holdings_value, total_value
        FROM portfolio_value_history
        WHERE user_id = %s
    """
    params = [user_id]
    if since is not None:
        sql += " AND recorded_at >= %s"
        params.append(since)
    sql += " ORDER BY recorded_at, id"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    with cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchall()


def latest(user_id):
    """The most recent sample, or None if the account has never been valued."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT recorded_at, cash, holdings_value, total_value
            FROM portfolio_value_history
            WHERE user_id = %s
            ORDER BY recorded_at DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        return cur.fetchone()


def earliest(user_id):
    """The first sample, which a return is measured from."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT recorded_at, cash, holdings_value, total_value
            FROM portfolio_value_history
            WHERE user_id = %s
            ORDER BY recorded_at, id
            LIMIT 1
            """,
            (user_id,),
        )
        return cur.fetchone()


def count(user_id):
    with cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM portfolio_value_history WHERE user_id = %s",
            (user_id,),
        )
        return cur.fetchone()[0]


def latest_for_everyone():
    """One row per account: its newest sample.

    DISTINCT ON is what makes this a single indexed pass rather than a
    correlated subquery per account — it walks
    portfolio_value_history_user_time_idx and takes the first row in each
    user's group. This is the leaderboard's whole data source; ranking by
    re-pricing every holding per viewer would mean a market-data call per
    page load.
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (user_id)
                   user_id, recorded_at, cash, holdings_value, total_value
            FROM portfolio_value_history
            ORDER BY user_id, recorded_at DESC, id DESC
            """
        )
        return cur.fetchall()

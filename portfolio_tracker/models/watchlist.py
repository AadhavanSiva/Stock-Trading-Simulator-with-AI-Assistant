"""Tickers an account follows without owning.

Deliberately not a flag on `portfolio`: a holding has shares and a cost
basis and a watched ticker has neither, so carrying them in one table
would mean every query that reads a position learning to exclude the rows
that are not one.
"""
from portfolio_tracker.db import cursor


def add(user_id, symbol):
    """Follow a ticker. Returns True if it was not already followed.

    ON CONFLICT DO NOTHING rather than a prior SELECT: two clicks in quick
    succession would both pass a check-then-insert, and the unique
    constraint is what actually settles it.
    """
    with cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO watchlist (user_id, symbol)
            VALUES (%s, %s)
            ON CONFLICT (user_id, symbol) DO NOTHING
            """,
            (user_id, symbol.upper()),
        )
        return cur.rowcount > 0


def remove(user_id, symbol):
    """Stop following. Returns True if it was being followed."""
    with cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM watchlist WHERE user_id = %s AND symbol = %s",
            (user_id, symbol.upper()),
        )
        return cur.rowcount > 0


def contains(user_id, symbol):
    with cursor() as cur:
        cur.execute(
            "SELECT 1 FROM watchlist WHERE user_id = %s AND symbol = %s",
            (user_id, symbol.upper()),
        )
        return cur.fetchone() is not None


def symbols(user_id):
    with cursor() as cur:
        cur.execute(
            "SELECT symbol FROM watchlist WHERE user_id = %s ORDER BY symbol",
            (user_id,),
        )
        return [row[0] for row in cur.fetchall()]


def entries(user_id):
    """Followed tickers with their prices, for the watchlist page.

    LEFT JOIN so a ticker whose stock row has no price yet still appears,
    the same way an unpriced holding does, rather than vanishing from a
    list the person put it on themselves.
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT watchlist.symbol, stocks.company_name,
                   stocks.current_price, stocks.previous_close,
                   watchlist.added_at,
                   EXISTS (
                       SELECT 1 FROM portfolio
                       WHERE portfolio.user_id = watchlist.user_id
                         AND portfolio.symbol = watchlist.symbol
                   ) AS owned
            FROM watchlist
            LEFT JOIN stocks ON stocks.symbol = watchlist.symbol
            WHERE watchlist.user_id = %s
            ORDER BY watchlist.symbol
            """,
            (user_id,),
        )
        return cur.fetchall()


def count(user_id):
    with cursor() as cur:
        cur.execute("SELECT count(*) FROM watchlist WHERE user_id = %s", (user_id,))
        return cur.fetchone()[0]

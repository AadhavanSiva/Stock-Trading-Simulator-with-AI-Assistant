from decimal import Decimal

import pandas as pd
from psycopg2.extras import execute_values

from portfolio_tracker.db import cursor
from portfolio_tracker.services.market_data import get_price_history


def _price(value):
    """OHLC cell -> Decimal, or None when the API left a gap (NaN)."""
    if value is None or pd.isna(value):
        return None
    return Decimal(str(value))


def _volume(value):
    """Volume -> int, or None. int(NaN) raises ValueError, so check first."""
    if value is None or pd.isna(value):
        return None
    return int(value)


def load_history_for_symbol(symbol, period="6mo"):
    """Fetch OHLCV history for one symbol and bulk-insert it.

    Returns the number of rows newly inserted. Existing (symbol, date)
    rows are skipped, so this is safe to re-run.
    """
    hist = get_price_history(symbol, period=period)
    if hist is None or hist.empty:
        return 0

    records = [
        (
            symbol,
            date.date(),
            _price(row["Open"]),
            _price(row["High"]),
            _price(row["Low"]),
            _price(row["Close"]),
            _volume(row["Volume"]),
        )
        for date, row in hist.iterrows()
    ]

    # A day with no close is a gap in the feed, not a data point.
    records = [r for r in records if r[5] is not None]
    if not records:
        return 0

    with cursor(commit=True) as cur:
        execute_values(
            cur,
            """
            INSERT INTO price_history (symbol, date, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (symbol, date) DO NOTHING
            """,
            records,
        )
        # rowcount is the rows actually inserted; len(records) counted the
        # rows *sent*, so re-running reported work that never happened.
        return cur.rowcount


def get_recent_averages(days=30):
    """Average closing price per symbol over the last N days."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT symbol, ROUND(AVG(close), 2)
            FROM price_history
            WHERE date >= CURRENT_DATE - (%s * INTERVAL '1 day')
              AND close IS NOT NULL
            GROUP BY symbol
            ORDER BY symbol
            """,
            (days,),
        )
        return cur.fetchall()


def get_high_low():
    """Highest and lowest close per symbol across all stored history."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT symbol, MAX(close), MIN(close)
            FROM price_history
            WHERE close IS NOT NULL
            GROUP BY symbol
            ORDER BY symbol
            """
        )
        return cur.fetchall()

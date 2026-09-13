from decimal import Decimal

import pandas as pd
from psycopg2.extras import execute_values

from portfolio_tracker.db import cursor
from portfolio_tracker.services.market_data import get_intraday, get_price_history


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
        inserted = execute_values(
            cur,
            """
            INSERT INTO price_history (symbol, date, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (symbol, date) DO NOTHING
            RETURNING 1
            """,
            records,
            fetch=True,
        )
        # Counted from RETURNING, not cur.rowcount. execute_values sends rows
        # in pages of 100, and rowcount only reflects the last page — so a
        # full-history load of 11,529 rows reported 29. RETURNING yields only
        # rows actually inserted (conflicts return nothing), across every page.
        return len(inserted)


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


def get_series(symbol, since=None):
    """Daily closing prices for one symbol, oldest first.

    `since` is a date; None means everything stored. Returns
    [(date, close), ...] — the shape a line chart needs.
    """
    with cursor() as cur:
        if since is None:
            cur.execute(
                """
                SELECT date, close FROM price_history
                WHERE symbol = %s AND close IS NOT NULL
                ORDER BY date
                """,
                (symbol,),
            )
        else:
            cur.execute(
                """
                SELECT date, close FROM price_history
                WHERE symbol = %s AND close IS NOT NULL AND date >= %s
                ORDER BY date
                """,
                (symbol, since),
            )
        return cur.fetchall()


def get_range_stats(symbol, since=None):
    """Low, high and average close over a window. Zeros when there is no data.

    Written as two complete statements rather than one assembled from a
    fragment. The fragment would have been a fixed literal and perfectly
    safe, but "no SQL is ever built by string formatting" is a rule worth
    keeping absolute — the moment it has one exception, it has others.
    """
    with cursor() as cur:
        if since is None:
            cur.execute(
                """
                SELECT MIN(close), MAX(close), ROUND(AVG(close), 2), COUNT(*)
                FROM price_history
                WHERE symbol = %s AND close IS NOT NULL
                """,
                (symbol,),
            )
        else:
            cur.execute(
                """
                SELECT MIN(close), MAX(close), ROUND(AVG(close), 2), COUNT(*)
                FROM price_history
                WHERE symbol = %s AND close IS NOT NULL AND date >= %s
                """,
                (symbol, since),
            )
        return cur.fetchone()


def load_intraday_for_symbol(symbol, period="1d", interval="5m"):
    """Fetch and store intraday bars. Returns the number of new rows.

    Re-running is cheap and safe: existing (symbol, ts) rows are skipped,
    so this tops up the cache rather than duplicating it.
    """
    frame = get_intraday(symbol, period=period, interval=interval)
    if frame is None or frame.empty:
        return 0

    records = []
    for ts, row in frame.iterrows():
        close = _price(row["Close"])
        if close is None:
            continue
        # Timestamps arrive tz-aware from Yahoo; the column is TIMESTAMPTZ,
        # so they are stored as the instants they actually are.
        records.append((symbol, ts.to_pydatetime(), close))

    if not records:
        return 0

    with cursor(commit=True) as cur:
        inserted = execute_values(
            cur,
            """
            INSERT INTO price_intraday (symbol, ts, close)
            VALUES %s
            ON CONFLICT (symbol, ts) DO NOTHING
            RETURNING 1
            """,
            records,
            fetch=True,
        )
        # RETURNING rather than rowcount: see load_history_for_symbol.
        return len(inserted)


def get_intraday_series(symbol, since=None):
    """Intraday closes for one symbol, oldest first: [(ts, close), ...]."""
    with cursor() as cur:
        if since is None:
            cur.execute(
                "SELECT ts, close FROM price_intraday WHERE symbol = %s ORDER BY ts",
                (symbol,),
            )
        else:
            cur.execute(
                """
                SELECT ts, close FROM price_intraday
                WHERE symbol = %s AND ts >= %s
                ORDER BY ts
                """,
                (symbol, since),
            )
        return cur.fetchall()


def get_intraday_sessions(symbol, sessions):
    """Intraday closes for the most recent `sessions` trading days.

    "1 day" has to mean the latest trading session, not the last 24 hours.
    Filtering by wall-clock time returns nothing all weekend, on holidays,
    and before the open — Friday's bars are already more than a day old by
    Saturday morning. Anchoring to the dates actually present in the data
    gives the session the reader means.

    Days are counted in exchange time (US Eastern), since that is where a
    trading day begins and ends.
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT ts, close FROM price_intraday
            WHERE symbol = %s
              AND (ts AT TIME ZONE 'America/New_York')::date >= (
                  SELECT MIN(day) FROM (
                      SELECT DISTINCT (ts AT TIME ZONE 'America/New_York')::date AS day
                      FROM price_intraday
                      WHERE symbol = %s
                      ORDER BY day DESC
                      LIMIT %s
                  ) recent
              )
            ORDER BY ts
            """,
            (symbol, symbol, sessions),
        )
        return cur.fetchall()

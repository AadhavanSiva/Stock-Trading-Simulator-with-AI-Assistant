from psycopg2.extras import execute_values

from portfolio_tracker.db import get_connection
from portfolio_tracker.services.market_data import get_price_history


def load_history_for_symbol(symbol, period="6mo"):
    """Fetch OHLCV history for one symbol and bulk-insert it.

    Returns the number of rows sent to the database. Existing
    (symbol, date) rows are skipped, so this is safe to re-run.
    """
    hist = get_price_history(symbol, period=period)
    if hist.empty:
        return 0

    records = [
        (
            symbol,
            date.date(),
            row["Open"],
            row["High"],
            row["Low"],
            row["Close"],
            int(row["Volume"]),
        )
        for date, row in hist.iterrows()
    ]

    conn = get_connection()
    cur = conn.cursor()
    try:
        execute_values(
            cur,
            """
            INSERT INTO price_history (symbol, date, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (symbol, date) DO NOTHING
            """,
            records,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return len(records)


def get_recent_averages(days=30):
    """Average closing price per symbol over the last N days."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT symbol, ROUND(AVG(close), 2)
        FROM price_history
        WHERE date >= CURRENT_DATE - (%s * INTERVAL '1 day')
        GROUP BY symbol
        ORDER BY symbol
        """,
        (days,),
    )
    results = cur.fetchall()
    cur.close()
    conn.close()
    return results


def get_high_low():
    """Highest and lowest close per symbol across all stored history."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT symbol, MAX(close), MIN(close)
        FROM price_history
        GROUP BY symbol
        ORDER BY symbol
        """
    )
    results = cur.fetchall()
    cur.close()
    conn.close()
    return results
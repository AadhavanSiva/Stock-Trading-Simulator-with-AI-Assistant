from decimal import Decimal
from zoneinfo import ZoneInfo

from psycopg2.extras import execute_values

from portfolio_tracker.db import cursor
from portfolio_tracker.services.market_data import get_intraday, get_price_history

# A trading day is the exchange's day, not UTC's.
EXCHANGE_TZ = ZoneInfo("America/New_York")


# market_data hands back Bar tuples whose prices are already Decimal or
# None, so the NaN-sniffing these two used to do belongs to the parsing
# layer now and not here. They stay as the one place that decides what an
# absent figure means, and to keep the call sites reading the same.

def _price(value):
    """A bar's OHLC figure, or None where the feed had a gap."""
    if value is None:
        return None
    value = Decimal(value)
    return value if value.is_finite() else None


def _volume(value):
    """A bar's volume as an int, or None where it is absent."""
    return None if value is None else int(value)


def load_history_for_symbol(symbol, period="6mo"):
    """Fetch OHLCV history for one symbol and bulk-insert it.

    Returns the number of rows newly inserted. Existing (symbol, date)
    rows are skipped, so this is safe to re-run.
    """
    bars = get_price_history(symbol, period=period)
    if not bars:
        return 0

    records = [
        (
            symbol,
            # A daily bar is stamped at the session's open in UTC. The
            # date wanted is the trading day it belongs to, which is the
            # exchange's, so the instant is converted before the date is
            # taken — otherwise a bar opening at 13:30 UTC on one day and
            # one at 00:30 UTC could land on different calendar dates than
            # the sessions they describe.
            bar.ts.astimezone(EXCHANGE_TZ).date(),
            _price(bar.open),
            _price(bar.high),
            _price(bar.low),
            _price(bar.close),
            _volume(bar.volume),
        )
        for bar in bars
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
    bars = get_intraday(symbol, period=period, interval=interval)
    if not bars:
        return 0

    records = []
    for bar in bars:
        close = _price(bar.close)
        if close is None:
            continue
        # Timestamps arrive tz-aware from Alpaca; the column is
        # TIMESTAMPTZ, so they are stored as the instants they actually
        # are and the session grouping stays correct in exchange time.
        records.append((symbol, bar.ts, close))

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


def load_full_history_for_symbol(symbol):
    """Download every available day, then record that history is complete.

    The marker is only written after the download succeeds, so a failed or
    interrupted fetch leaves the stock correctly flagged as partial.
    """
    from portfolio_tracker.models import stocks

    added = load_history_for_symbol(symbol, period="max")
    stocks.mark_full_history_loaded(symbol)
    return added


def earliest_date(symbol):
    """The first stored trading day for a symbol, or None."""
    with cursor() as cur:
        cur.execute(
            "SELECT MIN(date) FROM price_history WHERE symbol = %s",
            (symbol,),
        )
        return cur.fetchone()[0]

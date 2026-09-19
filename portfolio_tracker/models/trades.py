"""The append-only trade log, and the realized gains derived from it.

`record` is the odd one out in this package: it takes a cursor rather than
opening its own. That is deliberate. A trade row must be written in the
same transaction that moves the cash and the shares — if it opened its own
connection, a crash between the two would leave a position with no trade
behind it, or a trade for money that never moved. Taking the caller's
cursor makes "same transaction" a property of the signature rather than a
comment nobody reads. The only callers are record_purchase and record_sale
in models/portfolio.py, both already inside a locked transaction.

Nothing here updates or deletes. The database enforces that too (see the
trades_no_rewrite trigger in schema.sql), so this module having no such
function is a statement of intent rather than the actual guarantee.
"""
from decimal import Decimal

from portfolio_tracker.db import cursor

# Rows per page in the full history view.
PAGE_SIZE = 25


def record(cur, user_id, symbol, side, shares, price, total_value, cost_basis=None):
    """Append one trade, using the caller's open transaction.

    `cost_basis` is the weighted-average cost per share at the moment of a
    sale and is required on a sell, rejected on a buy — the database checks
    both. Returns the new trade's id.
    """
    cur.execute(
        """
        INSERT INTO trades (user_id, symbol, side, shares, price, total_value, cost_basis)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (user_id, symbol, side, shares, price, total_value, cost_basis),
    )
    return cur.fetchone()[0]


# ------------------------------------------------------------------ reading

def _select(where, params, limit=None, offset=None):
    sql = f"""
        SELECT trades.id, trades.symbol, stocks.company_name, trades.side,
               trades.shares, trades.price, trades.total_value,
               trades.cost_basis, trades.backfilled, trades.traded_at
        FROM trades
        LEFT JOIN stocks ON stocks.symbol = trades.symbol
        WHERE {where}
        ORDER BY trades.traded_at DESC, trades.id DESC
    """
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        params = params + (limit, offset or 0)
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def recent(user_id, limit=5):
    """The newest trades, for the portfolio page's activity section."""
    return _select("trades.user_id = %s", (user_id,), limit=limit, offset=0)


def page(user_id, page_number=1, page_size=PAGE_SIZE):
    """One page of the full history, newest first.

    Ordering is by time then id, so a trade cannot appear on two pages
    because two rows happened to share a timestamp. The caller asks for
    count() separately when it needs the total.
    """
    page_number = max(1, int(page_number))
    return _select(
        "trades.user_id = %s", (user_id,),
        limit=page_size, offset=(page_number - 1) * page_size,
    )


def count(user_id):
    with cursor() as cur:
        cur.execute("SELECT count(*) FROM trades WHERE user_id = %s", (user_id,))
        return cur.fetchone()[0]


def for_symbol(user_id, symbol):
    """Every trade in one ticker, newest first."""
    return _select(
        "trades.user_id = %s AND trades.symbol = %s", (user_id, symbol.upper()),
    )


# --------------------------------------------------------- realized gains

def realized_by_symbol(user_id):
    """Realized gain per ticker: {symbol: (gain, proceeds, shares_sold)}.

    Only sells realize anything — a buy moves money into a position
    without settling whether it was a good one. The gain is the difference
    between what each sale fetched and what those shares had cost on
    average, which is why `cost_basis` is captured on the row at sale time.
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT symbol,
                   sum((price - cost_basis) * shares) AS gain,
                   sum(total_value)                   AS proceeds,
                   sum(shares)                        AS shares_sold
            FROM trades
            WHERE user_id = %s AND side = 'sell'
            GROUP BY symbol
            ORDER BY symbol
            """,
            (user_id,),
        )
        return {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}


def realized_total(user_id):
    """Realized gain across every closed or trimmed position."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT coalesce(sum((price - cost_basis) * shares), 0)
            FROM trades
            WHERE user_id = %s AND side = 'sell'
            """,
            (user_id,),
        )
        return cur.fetchone()[0] or Decimal(0)

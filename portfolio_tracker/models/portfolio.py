from collections import namedtuple
from decimal import Decimal, ROUND_HALF_UP

from portfolio_tracker.db import cursor
from portfolio_tracker.errors import InsufficientFunds, UnknownUser
from portfolio_tracker.models import trades

CENTS = Decimal("0.01")

# What a sale settled at. `cost_basis` is the weighted-average cost of the
# shares sold, read while the holding was locked — the caller needs it to
# report the realized gain, and reading it separately afterwards would race
# a concurrent buy that changes the average.
SaleResult = namedtuple("SaleResult", "sold remaining cash cost_basis")


def to_money(value):
    """Round to whole cents. Cash is money, so it settles at 2 decimals even
    when fractional shares produce a longer product."""
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def get_portfolio_symbols(user_id):
    with cursor() as cur:
        cur.execute(
            "SELECT symbol FROM portfolio WHERE user_id = %s ORDER BY symbol",
            (user_id,),
        )
        return [row[0] for row in cur.fetchall()]


def get_holding(user_id, symbol):
    """Return (shares, purchase_price) for a symbol, or None if not held."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT shares, purchase_price FROM portfolio
            WHERE user_id = %s AND symbol = %s
            """,
            (user_id, symbol),
        )
        return cur.fetchone()


def get_holdings_with_prices(user_id):
    """Holdings with current price and gain/loss.

    LEFT JOIN so a holding whose stock row has no price yet still shows up
    (with current_price and gain_loss as None) instead of silently
    disappearing from the report.
    """
    with cursor() as cur:
        cur.execute("""
            SELECT portfolio.symbol, portfolio.shares, portfolio.purchase_price,
                   stocks.current_price,
                   (stocks.current_price - portfolio.purchase_price) * portfolio.shares AS gain_loss
            FROM portfolio
            LEFT JOIN stocks ON portfolio.symbol = stocks.symbol
            WHERE portfolio.user_id = %s
            ORDER BY portfolio.symbol;
        """, (user_id,))
        return cur.fetchall()


def get_holdings_with_details(user_id):
    """As above, with the company name inserted after the symbol:
        (symbol, company_name, shares, purchase_price, current_price,
         gain_loss, previous_close)
    """
    with cursor() as cur:
        cur.execute("""
            SELECT portfolio.symbol, stocks.company_name,
                   portfolio.shares, portfolio.purchase_price,
                   stocks.current_price,
                   (stocks.current_price - portfolio.purchase_price) * portfolio.shares AS gain_loss,
                   stocks.previous_close
            FROM portfolio
            LEFT JOIN stocks ON portfolio.symbol = stocks.symbol
            WHERE portfolio.user_id = %s
            ORDER BY portfolio.symbol;
        """, (user_id,))
        return cur.fetchall()


def record_purchase(user_id, symbol, company_name, price, shares):
    """Buy shares and pay for them, as one indivisible operation.

    Three tables move together here: cash leaves `users`, the ticker is
    registered in `stocks`, and the position grows in `portfolio`. Split
    across separate transactions, a failure partway through could debit
    cash without delivering shares, or the reverse.

    The user row is locked FOR UPDATE before the balance is checked, so two
    concurrent buys cannot both pass an affordability test against the same
    starting balance and overdraw the account.

    Raises InsufficientFunds if the purchase costs more than the balance.
    Returns (total_shares, average_cost, remaining_cash).
    """
    shares = Decimal(shares)
    price = Decimal(price)
    cost = to_money(shares * price)

    with cursor(commit=True) as cur:
        cur.execute("SELECT cash FROM users WHERE id = %s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if row is None:
            raise UnknownUser(f"No account with id {user_id}.")

        available = row[0]
        if cost > available:
            raise InsufficientFunds(cost, available)

        cur.execute(
            "UPDATE users SET cash = cash - %s WHERE id = %s RETURNING cash",
            (cost, user_id),
        )
        remaining_cash = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO stocks (symbol, company_name, current_price)
            VALUES (%s, %s, %s)
            ON CONFLICT (symbol) DO UPDATE SET
                company_name  = COALESCE(EXCLUDED.company_name, stocks.company_name),
                current_price = COALESCE(EXCLUDED.current_price, stocks.current_price)
            """,
            (symbol, company_name, price),
        )
        cur.execute(
            """
            INSERT INTO portfolio (user_id, symbol, shares, purchase_price)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, symbol) DO UPDATE SET
                purchase_price =
                    (portfolio.shares * portfolio.purchase_price
                     + EXCLUDED.shares * EXCLUDED.purchase_price)
                    / (portfolio.shares + EXCLUDED.shares),
                shares = portfolio.shares + EXCLUDED.shares
            RETURNING shares, purchase_price
            """,
            (user_id, symbol, shares, price),
        )
        total_shares, average_cost = cur.fetchone()

        # Same cursor, so the same transaction: the ledger entry and the
        # money it describes commit together or not at all.
        trades.record(cur, user_id, symbol, "buy", shares, price, cost)

        return total_shares, average_cost, remaining_cash


def record_sale(user_id, symbol, price, shares_to_sell):
    """Sell shares and receive the proceeds, as one indivisible operation.

    The holding is locked FOR UPDATE and re-checked inside the transaction,
    so a position that changed since the form was rendered fails here rather
    than overselling. Cash is credited in the same transaction that removes
    the shares.

    Returns a SaleResult, or SaleResult(0, None, None, None) if the sale
    was not valid.
    """
    nothing = SaleResult(0, None, None, None)
    if shares_to_sell is None:
        return nothing
    shares_to_sell = Decimal(shares_to_sell)
    if not shares_to_sell.is_finite() or shares_to_sell <= 0:
        return nothing

    price = Decimal(price)
    proceeds = to_money(shares_to_sell * price)

    with cursor(commit=True) as cur:
        # The account row is locked first, before the holding, because
        # record_purchase locks them in that order too. Taking the same two
        # locks in opposite orders is the textbook deadlock: a buy holding
        # `users` and waiting for `portfolio`, against a sell holding
        # `portfolio` and waiting for `users`, and PostgreSQL resolves it by
        # aborting one of them. That is reachable here — one account, a buy
        # and a sell of the same position arriving together on two of
        # gunicorn's threads. This line is the whole fix; see
        # tests/test_concurrency.py, which drives both orders and fails if
        # either deadlocks.
        cur.execute("SELECT 1 FROM users WHERE id = %s FOR UPDATE", (user_id,))

        cur.execute(
            """
            SELECT id, shares, purchase_price FROM portfolio
            WHERE user_id = %s AND symbol = %s
            FOR UPDATE
            """,
            (user_id, symbol),
        )
        row = cur.fetchone()
        if row is None:
            return nothing

        holding_id, current_shares, cost_basis = row
        if shares_to_sell > current_shares:
            return nothing

        remaining = current_shares - shares_to_sell
        if remaining == 0:
            cur.execute("DELETE FROM portfolio WHERE id = %s", (holding_id,))
        else:
            cur.execute(
                "UPDATE portfolio SET shares = %s WHERE id = %s",
                (remaining, holding_id),
            )

        cur.execute(
            "UPDATE users SET cash = cash + %s WHERE id = %s RETURNING cash",
            (proceeds, user_id),
        )
        new_cash = cur.fetchone()[0]

        # The cost basis recorded here is the one that was locked above, so
        # the realized gain on this row is settled by the same transaction
        # that moved the shares and the cash.
        trades.record(cur, user_id, symbol, "sell", shares_to_sell, price,
                      proceeds, cost_basis=cost_basis)

        return SaleResult(shares_to_sell, remaining, new_cash, cost_basis)

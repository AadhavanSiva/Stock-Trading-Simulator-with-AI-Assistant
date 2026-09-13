from portfolio_tracker.db import cursor


def upsert_stock(symbol, company_name, current_price):
    """Insert a stock, refreshing its name and price if it already exists.

    This used to be ON CONFLICT DO NOTHING, which meant a symbol added a
    second time kept its original price forever and every gain/loss figure
    in the report was computed against a stale quote. COALESCE keeps a
    known-good name/price rather than overwriting it with a NULL lookup.
    """
    with cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO stocks (symbol, company_name, current_price)
            VALUES (%s, %s, %s)
            ON CONFLICT (symbol) DO UPDATE SET
                company_name  = COALESCE(EXCLUDED.company_name, stocks.company_name),
                current_price = COALESCE(EXCLUDED.current_price, stocks.current_price)
            """,
            (symbol, company_name, current_price)
        )


def update_stock_price(symbol, price):
    """Update a symbol's current price. Returns True if the symbol existed."""
    with cursor(commit=True) as cur:
        cur.execute(
            "UPDATE stocks SET current_price = %s WHERE symbol = %s",
            (price, symbol)
        )
        return cur.rowcount > 0


def get_all_stocks():
    with cursor() as cur:
        cur.execute("SELECT symbol FROM stocks ORDER BY symbol")
        return [row[0] for row in cur.fetchall()]

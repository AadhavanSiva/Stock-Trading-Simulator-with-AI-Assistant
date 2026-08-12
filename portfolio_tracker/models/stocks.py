from portfolio_tracker.db import get_connection

def upsert_stock(symbol, company_name, current_price):
    """Insert a stock if it doesn't exist yet; do nothing if it already does."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO stocks (symbol, company_name, current_price)
        VALUES (%s, %s, %s)
        ON CONFLICT (symbol) DO NOTHING
        """,
        (symbol, company_name, current_price)
    )
    conn.commit()
    cur.close()
    conn.close()

def update_stock_price(symbol, price):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE stocks SET current_price = %s WHERE symbol = %s",
        (price, symbol)
    )
    conn.commit()
    cur.close()
    conn.close()

def get_all_stocks():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT symbol FROM stocks")
    symbols = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return symbols

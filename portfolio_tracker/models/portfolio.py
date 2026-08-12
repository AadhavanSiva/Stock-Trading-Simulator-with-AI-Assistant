from portfolio_tracker.db import get_connection

def get_portfolio_symbols():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT symbol FROM portfolio")
    symbols = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return symbols

def add_holding(symbol, shares, purchase_price):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO portfolio (symbol, shares, purchase_price) VALUES (%s, %s, %s)",
        (symbol, shares, purchase_price)
    )
    conn.commit()
    cur.close()
    conn.close()

def remove_holding(symbol):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM portfolio WHERE symbol = %s", (symbol,))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return deleted > 0

def get_holdings_with_prices():
    """Joins portfolio + stocks, returns holdings with current price and gain/loss."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT portfolio.symbol, portfolio.shares, portfolio.purchase_price,
               stocks.current_price,
               (stocks.current_price - portfolio.purchase_price) * portfolio.shares AS gain_loss
        FROM portfolio
        JOIN stocks ON portfolio.symbol = stocks.symbol
        ORDER BY portfolio.symbol;
    """)
    results = cur.fetchall()
    cur.close()
    conn.close()
    return results


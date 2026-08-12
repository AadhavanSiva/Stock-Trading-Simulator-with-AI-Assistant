import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host="localhost",
    database="practice",
    user="postgres",
    password=os.getenv("DB_PASSWORD"),
    port="5432"
)
cur = conn.cursor()

print("=" * 60)
print("PORTFOLIO SUMMARY REPORT")
print("=" * 60)

# Current holdings with live gain/loss
cur.execute("""
    SELECT portfolio.symbol,
           portfolio.shares,
           portfolio.purchase_price,
           stocks.current_price,
           (stocks.current_price - portfolio.purchase_price) * portfolio.shares AS gain_loss
    FROM portfolio
    JOIN stocks ON portfolio.symbol = stocks.symbol
    ORDER BY portfolio.symbol;
""")
holdings = cur.fetchall()

print(f"\n{'Symbol':<8}{'Shares':<10}{'Bought At':<12}{'Current':<12}{'Gain/Loss':<12}")
print("-" * 54)
total_value = 0
total_gain_loss = 0
for symbol, shares, purchase_price, current_price, gain_loss in holdings:
    value = shares * current_price
    total_value += value
    total_gain_loss += gain_loss
    print(f"{symbol:<8}{shares:<10}${purchase_price:<11.2f}${current_price:<11.2f}${gain_loss:<11.2f}")

print("-" * 54)
print(f"Total Portfolio Value: ${total_value:.2f}")
print(f"Total Gain/Loss: ${total_gain_loss:.2f}")

# 30-day average close per stock
cur.execute("""
    SELECT symbol, ROUND(AVG(close), 2)
    FROM price_history
    WHERE date >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY symbol
    ORDER BY symbol;
""")
print(f"\n{'30-Day Averages':<20}")
print("-" * 30)
for symbol, avg_close in cur.fetchall():
    print(f"{symbol:<8}${avg_close}")

# All-time high/low within the loaded history window
cur.execute("""
    SELECT symbol, MAX(close), MIN(close)
    FROM price_history
    GROUP BY symbol
    ORDER BY symbol;
""")
print(f"\n{'6-Month High/Low':<20}")
print("-" * 30)
for symbol, high, low in cur.fetchall():
    print(f"{symbol:<8}High: ${high:<10.2f}Low: ${low:<10.2f}")

cur.close()
conn.close()
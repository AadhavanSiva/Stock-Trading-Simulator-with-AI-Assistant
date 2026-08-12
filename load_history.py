import yfinance as yf
import psycopg2
from psycopg2.extras import execute_values
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

# Get all symbols from your portfolio
cur.execute("SELECT symbol FROM portfolio")
symbols = [row[0] for row in cur.fetchall()]

for symbol in symbols:
    try:
        print(f"Pulling history for {symbol}...")
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="6mo")  # last 6 months of daily data

        if hist.empty:
            print(f"⚠ No history found for {symbol}, skipping")
            continue

        # Build a list of tuples: one per row of data
        records = []
        for date, row in hist.iterrows():
            records.append((
                symbol,
                date.date(),
                row['Open'],
                row['High'],
                row['Low'],
                row['Close'],
                int(row['Volume'])
            ))

        # Bulk insert all rows for this stock in one operation
        execute_values(
            cur,
            """
            INSERT INTO price_history (symbol, date, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (symbol, date) DO NOTHING
            """,
            records
        )

        print(f"Inserted {len(records)} rows for {symbol}")

    except Exception as e:
        print(f"⚠ Error loading history for {symbol}: {e}")
        continue

conn.commit()
print("Historical data load complete.")

cur.close()
conn.close()
import yfinance as yf
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

try:
    cur.execute("SELECT symbol FROM portfolio")
    rows = cur.fetchall()
    symbols = [row[0] for row in rows]

    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            live_price = ticker.info.get('currentPrice') or ticker.info.get('regularMarketPrice')

            if live_price is None:
                print(f"⚠ Skipping {symbol}: no price data available")
                continue

            print(f"Live {symbol}: ${live_price}")

            cur.execute(
                "UPDATE stocks SET current_price = %s WHERE symbol = %s",
                (live_price, symbol)
            )

        except Exception as e:
            print(f"⚠ Error updating {symbol}: {e}")
            continue

    conn.commit()
    print("All prices updated successfully.")

except Exception as e:
    conn.rollback()
    print(f"Something went wrong, rolling back all changes: {e}")

cur.close()
conn.close()
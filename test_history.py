import yfinance as yf

ticker = yf.Ticker("NVDA")
hist = ticker.history(period="1mo")  # last 1 month of daily data
print(hist)
print(hist.columns)
print(len(hist))

for date, row in hist.iterrows():
    print(date.date(), row['Open'], row['High'], row['Low'], row['Close'], row['Volume'])
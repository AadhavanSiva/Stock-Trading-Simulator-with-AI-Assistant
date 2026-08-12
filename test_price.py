import yfinance as yf

ticker = yf.Ticker("NVDA")
price = ticker.info['currentPrice']
print(f"NVDA current price: ${price}")
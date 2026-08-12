import yfinance as yf

def get_live_price(symbol):
    ticker = yf.Ticker(symbol)
    return ticker.info.get('currentPrice') or ticker.info.get('regularMarketPrice')

def get_company_name(symbol):
    ticker = yf.Ticker(symbol)
    return ticker.info.get('longName', symbol)

def get_price_history(symbol, period="6mo"):
    ticker = yf.Ticker(symbol)
    return ticker.history(period=period)

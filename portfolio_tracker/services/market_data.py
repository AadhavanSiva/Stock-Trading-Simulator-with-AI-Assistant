from decimal import Decimal, InvalidOperation

import yfinance as yf


def _to_decimal(value):
    """Convert a yfinance float to Decimal without binary-float dust.

    Going through str() keeps the value the API actually reported
    (332.27) instead of its float expansion (332.27000000000001...),
    which matters because every price column is NUMERIC.
    """
    if value is None:
        return None
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return price if price.is_finite() else None


def get_quote(symbol):
    """Fetch price and company name in one lookup.

    Returns (price, company_name); price is None when the symbol has no
    usable quote. Callers that need both used to hit .info twice.
    """
    info = yf.Ticker(symbol).info or {}
    price = _to_decimal(info.get("currentPrice") or info.get("regularMarketPrice"))
    name = info.get("longName") or info.get("shortName") or symbol
    return price, name


def get_live_price(symbol):
    """Current price as a Decimal, or None if unavailable."""
    return get_quote(symbol)[0]


def get_company_name(symbol):
    return get_quote(symbol)[1]


def get_price_history(symbol, period="6mo"):
    return yf.Ticker(symbol).history(period=period)

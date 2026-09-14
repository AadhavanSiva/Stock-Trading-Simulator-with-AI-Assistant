"""The only module that talks to yfinance.

Every call has an explicit time limit, every failure is logged here with
the real error and re-raised as MarketDataUnavailable (whose message is safe
to show a person), and quote lookups are cached briefly so repeated page
views do not re-ask Yahoo for a price it gave a few seconds ago.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as DeadlineExceeded
from decimal import Decimal, InvalidOperation

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFPricesMissingError

from portfolio_tracker import config
from portfolio_tracker.errors import MarketDataUnavailable

log = logging.getLogger(__name__)

# yfinance's Ticker.info accepts no timeout (its requests use a fixed 30
# seconds, with retries), so each call runs on a worker thread and the
# caller stops waiting at the deadline. An abandoned request finishes in the
# background within yfinance's own limit; the pool bounds how many can.
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="market-data")

_QUOTE_CACHE_LIMIT = 500
_quotes = {}
_quotes_lock = threading.Lock()


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


def _with_deadline(symbol, seconds, fetch):
    """Run one yfinance call, giving up after `seconds`."""
    future = _pool.submit(fetch)
    try:
        return future.result(timeout=seconds)
    except DeadlineExceeded:
        future.cancel()
        log.warning("Yahoo Finance request for %s took longer than %ss; gave up", symbol, seconds)
        raise MarketDataUnavailable(symbol) from None


def _status_code(exc):
    return getattr(getattr(exc, "response", None), "status_code", None)


# ------------------------------------------------------------------ quotes

def clear_quote_cache():
    with _quotes_lock:
        _quotes.clear()


def get_quote(symbol, fresh=False):
    """Fetch price and company name in one lookup.

    Returns (price, company_name); price is None when the symbol has no
    usable quote. Raises MarketDataUnavailable when Yahoo cannot be reached.

    A result is reused for QUOTE_CACHE_SECONDS unless `fresh` is set.
    Anything that trades, or that the reader asked to refresh, passes
    fresh=True so a purchase is never priced from a cached quote. Failures
    are never cached.
    """
    key = (symbol or "").strip().upper()
    ttl = config.QUOTE_CACHE_SECONDS

    if not fresh and ttl > 0:
        with _quotes_lock:
            hit = _quotes.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]

    quote = _fetch_quote(symbol)

    if ttl > 0:
        with _quotes_lock:
            if key not in _quotes and len(_quotes) >= _QUOTE_CACHE_LIMIT:
                now = time.monotonic()
                for stale in [k for k, (expires, _) in _quotes.items() if expires <= now]:
                    del _quotes[stale]
                if len(_quotes) >= _QUOTE_CACHE_LIMIT:
                    del _quotes[next(iter(_quotes))]   # oldest entry
            _quotes[key] = (time.monotonic() + ttl, quote)
    return quote


def _fetch_quote(symbol):
    try:
        info = _with_deadline(symbol, config.MARKET_QUOTE_TIMEOUT,
                              lambda: yf.Ticker(symbol).info) or {}
    except MarketDataUnavailable:
        raise
    except Exception as exc:
        log.warning("Yahoo Finance quote for %s failed", symbol, exc_info=True)
        raise MarketDataUnavailable(symbol) from exc

    price = _to_decimal(info.get("currentPrice") or info.get("regularMarketPrice"))
    name = info.get("longName") or info.get("shortName") or symbol
    return price, name


def get_live_price(symbol, fresh=False):
    """Current price as a Decimal, or None if unavailable."""
    return get_quote(symbol, fresh=fresh)[0]


def get_company_name(symbol):
    return get_quote(symbol)[1]


# ----------------------------------------------------------------- history

def _history(symbol, **params):
    """Download bars, telling "Yahoo is down" apart from "no prices".

    raise_errors=True matters: by default yfinance logs a failed download
    and returns an empty frame, which the loaders would report as "0 new
    days" during an outage. (yfinance 1.5 marks the flag deprecated in
    favour of a global switch; that switch also changes how Ticker.info
    behaves, so the per-call flag is used here.)
    """
    timeout = config.MARKET_HISTORY_TIMEOUT
    fetch = lambda: yf.Ticker(symbol).history(timeout=timeout, raise_errors=True, **params)  # noqa: E731
    try:
        return _with_deadline(symbol, timeout, fetch)
    except MarketDataUnavailable:
        raise
    except YFPricesMissingError:
        # Yahoo answered and has no prices for that range: genuinely empty.
        log.info("Yahoo Finance has no prices for %s with %s", symbol, params)
        return pd.DataFrame()
    except Exception as exc:
        if _status_code(exc) == 404:
            log.info("Yahoo Finance does not know the symbol %s", symbol)
            return pd.DataFrame()
        log.warning("Yahoo Finance history for %s failed", symbol, exc_info=True)
        raise MarketDataUnavailable(symbol) from exc


def get_price_history(symbol, period="6mo"):
    return _history(symbol, period=period)


def get_intraday(symbol, period="1d", interval="5m"):
    """Fetch intraday bars for the short-range charts.

    Yahoo limits how far back each interval goes — roughly 7 days of
    1-minute bars and 60 days of coarser ones — so callers ask for a
    period the chosen interval can actually serve.
    """
    return _history(symbol, period=period, interval=interval)

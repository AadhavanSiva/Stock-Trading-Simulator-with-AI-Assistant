"""The only module that talks to Alpaca.

Every call has an explicit time limit, every failure is logged here with
the real error and re-raised as MarketDataUnavailable (whose message is
safe to show a person), and lookups are cached briefly so repeated page
views do not re-ask for a price given a few seconds ago.

Two of Alpaca's hosts are used, because the data they hold is split:

  * data.alpaca.markets serves prices — snapshots and bars.
  * api.alpaca.markets serves the asset catalogue, which is the only
    place a company's *name* lives, and the only endpoint that can say
    whether a symbol exists at all.

That split matters more than it looks. The bars endpoint answers 200 with
`"bars": null` for a symbol that does not exist, which is the same answer
it gives for a real symbol with no trading in the requested range. Asking
the catalogue first is what tells those two apart; see resolve_symbol.

Bars come back as a list of Bar, not a DataFrame. Alpaca returns JSON, so
there is nothing to parse into a frame and no reason for a caller to need
pandas to read one.
"""
import logging
import re
import threading
import time
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import requests

from portfolio_tracker import config
from portfolio_tracker.errors import (
    MarketDataCredentialsRejected, MarketDataUnavailable, UnknownSymbol,
)

log = logging.getLogger(__name__)

# One OHLCV bar. `ts` is timezone-aware UTC, as Alpaca sends it; the
# intraday session logic converts to exchange time in SQL, so what matters
# here is only that the offset is never lost.
Bar = namedtuple("Bar", "ts open high low close volume")

# Connection reuse across calls. Alpaca is HTTPS, so a fresh TCP and TLS
# handshake per quote would be most of the latency of a quote.
_session = requests.Session()

_QUOTE_CACHE_LIMIT = 500
_quotes = {}
_quotes_lock = threading.Lock()

# A company's name and whether it is tradable change on the order of
# corporate actions, not seconds, so this is cached far longer than a
# price. It also halves the request count: a quote needs the snapshot for
# the price and the catalogue for the name, and only the first is
# perishable.
_ASSET_CACHE_SECONDS = 24 * 60 * 60
_assets = {}
_assets_lock = threading.Lock()


# --------------------------------------------------------------- helpers

def _to_decimal(value):
    """A JSON number as a Decimal, without binary-float dust.

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


def _headers():
    key, secret = config.alpaca_credentials()
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
    }


def _timestamp(raw):
    """One RFC-3339 timestamp from Alpaca as an aware datetime.

    Alpaca sends nanosecond precision and a trailing "Z", and Python
    3.10's fromisoformat reads neither. Both are normalised here rather
    than reached for a dependency: the fraction is truncated to the six
    digits datetime can hold, and "Z" becomes an explicit +00:00.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    # Split off any trailing offset before touching the fraction, so a
    # "+00:00" is never mistaken for part of the nanoseconds.
    offset = ""
    for marker in ("+", "-"):
        position = text.rfind(marker)
        if position > 10:            # past the date, so it is an offset
            offset = text[position:]
            text = text[:position]
            break

    if "." in text:
        head, _, fraction = text.partition(".")
        text = f"{head}.{fraction[:6]}" if fraction[:6] else head

    try:
        moment = datetime.fromisoformat(text + offset)
    except ValueError:
        log.warning("Could not read an Alpaca timestamp: %r", raw)
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _request(url, params=None, timeout=None, symbol=None, allow_404=False):
    """One Alpaca call, with every documented failure turned into ours.

    The status codes come from Alpaca's own error table rather than from
    assumption: 401 for credentials, 403 for a resource the plan does not
    cover, 429 for the rate limit, 400 for a bad parameter, 5xx for their
    side. Worth stating that a rejected key is 401 here, because the
    Gemini client in this same project answers 400 for that — assuming two
    vendors agree on a status code is how that bug arrived.
    """
    timeout = timeout or config.MARKET_QUOTE_TIMEOUT
    # Built before the try, deliberately. A missing API key raises
    # ConfigurationError, and the catch-all below would otherwise convert
    # it into "the service didn't respond" — advice to retry, for the one
    # failure retrying can never fix.
    headers = _headers()
    try:
        response = _session.get(
            url,
            params=params,
            headers=headers,
            # (connect, read). A connection that never opens is a
            # different failure from a server thinking about it, and
            # neither may hold a page open indefinitely.
            timeout=(min(5.0, timeout), timeout),
        )
    except requests.Timeout:
        log.warning("Alpaca request for %s timed out after %ss (%s)",
                    symbol or "-", timeout, url)
        raise MarketDataUnavailable(symbol) from None
    except requests.RequestException as exc:
        log.warning("Alpaca request for %s failed", symbol or "-", exc_info=True)
        raise MarketDataUnavailable(symbol) from exc
    except Exception as exc:
        # Deliberately broad. Not everything a transport can raise is a
        # RequestException — an SSL library, a proxy shim or a bug in a
        # dependency can throw something else entirely, and whatever it
        # is, its text describes our internals and must not travel up to
        # a page. The real error is logged here with its traceback.
        log.error("Unexpected error calling Alpaca for %s", symbol or "-",
                  exc_info=True)
        raise MarketDataUnavailable(symbol) from exc

    if response.status_code == 200:
        try:
            return response.json()
        except ValueError as exc:
            log.warning("Alpaca returned a non-JSON body for %s: %r",
                        symbol or "-", response.text[:200])
            raise MarketDataUnavailable(symbol) from exc

    if response.status_code == 404 and allow_404:
        return None

    detail = response.text[:300]

    if response.status_code == 401:
        # A configuration problem, not an outage: it will not fix itself
        # on a retry, so it is logged at error and named for what it is.
        log.error(
            "Alpaca rejected the API credentials (401). Check "
            "ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY. Response: %s", detail
        )
        raise MarketDataCredentialsRejected(symbol)

    if response.status_code == 403:
        # Usually the data plan rather than the key: the free tier serves
        # the IEX feed, and asking for SIP — or for data newer than the
        # plan allows — is refused rather than quietly downgraded.
        log.error(
            "Alpaca refused the request (403) — usually the data plan. This app "
            "asks for the '%s' feed. Response: %s",
            config.ALPACA_DATA_FEED, detail,
        )
        raise MarketDataCredentialsRejected(symbol)

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        log.warning(
            "Alpaca rate limit hit (429) for %s%s. The free plan allows about "
            "200 requests a minute.",
            symbol or "-",
            f"; Retry-After {retry_after}s" if retry_after else "",
        )
        raise MarketDataUnavailable(symbol)

    if response.status_code == 400:
        log.warning("Alpaca rejected the request for %s (400): %s",
                    symbol or "-", detail)
        raise MarketDataUnavailable(symbol)

    log.warning("Alpaca returned %s for %s: %s",
                response.status_code, symbol or "-", detail)
    raise MarketDataUnavailable(symbol)


# --------------------------------------------------------- the catalogue

def clear_quote_cache():
    """Forget every cached quote, asset and the search catalogue."""
    with _quotes_lock:
        _quotes.clear()
    with _assets_lock:
        _assets.clear()
    with _catalogue_lock:
        _catalogue["assets"] = []
        _catalogue["expires"] = 0.0


def resolve_symbol(symbol, fresh=False):
    """Look a ticker up in the asset catalogue.

    Returns an asset dict, or None when Alpaca has never heard of it — the
    catalogue answers 404 for an unknown symbol, which is the one
    unambiguous "no such thing" the API offers. The price endpoints do not
    give that: bars for a nonexistent ticker arrive as 200 with
    `"bars": null`, exactly like a real ticker with no trades in range.
    """
    key = (symbol or "").strip().upper()
    if not key:
        return None

    if not fresh:
        with _assets_lock:
            hit = _assets.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]

    asset = _request(
        f"{config.ALPACA_TRADING_URL}/v2/assets/{key}",
        timeout=config.MARKET_QUOTE_TIMEOUT,
        symbol=key,
        allow_404=True,
    )

    with _assets_lock:
        if key not in _assets and len(_assets) >= _QUOTE_CACHE_LIMIT:
            _assets.clear()
        _assets[key] = (time.monotonic() + _ASSET_CACHE_SECONDS, asset)
    return asset


def symbol_is_tradable(symbol):
    """(ok, reason). `reason` is None when the symbol is fine.

    Alpaca lists assets it knows but will not trade — delisted names, and
    classes this app does not handle — so "exists" and "tradable" are two
    questions and both have to be asked.
    """
    asset = resolve_symbol(symbol)
    if asset is None:
        return False, (f"No market data found for '{symbol}'. "
                       f"Check the ticker and try again.")
    shown = asset.get("symbol") or symbol
    if asset.get("class") not in (None, "us_equity"):
        return False, f"{shown} is not a US-listed stock."
    if asset.get("status") != "active" or not asset.get("tradable"):
        return False, f"{shown} is not currently tradable."
    return True, None


def _company_name(symbol, asset=None):
    asset = asset if asset is not None else resolve_symbol(symbol)
    if not asset:
        return symbol
    return asset.get("name") or symbol


# ------------------------------------------------------------------ quotes

def get_quote(symbol, fresh=False):
    """Fetch price, company name and previous close in one lookup.

    Returns (price, company_name, previous_close); price is None when the
    symbol has no usable quote, and previous_close is None when Alpaca
    does not report one. Raises MarketDataUnavailable when Alpaca cannot
    be reached.

    All three describe the same moment on purpose: today's change is the
    difference between two of them, and taking them from separate lookups
    would measure across a window nobody asked for.

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

    quote = _fetch_quote(key)

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
    asset = resolve_symbol(symbol)
    if asset is None:
        # Not an outage — Alpaca answered, and the answer was "no such
        # ticker". The caller turns a None price into a message about the
        # symbol rather than about the service.
        return None, symbol, None

    snapshot = _request(
        f"{config.ALPACA_DATA_URL}/v2/stocks/{symbol}/snapshot",
        params={"feed": config.ALPACA_DATA_FEED},
        timeout=config.MARKET_QUOTE_TIMEOUT,
        symbol=symbol,
    ) or {}

    return (_latest_price(snapshot),
            _company_name(symbol, asset),
            _previous_close(snapshot))


def _latest_price(snapshot):
    """The most recent price the snapshot can supply.

    The preference order matters outside market hours. latestTrade is an
    actual transaction and is the truest answer when there is one;
    minuteBar and dailyBar close are aggregates of the same session; and
    prevDailyBar is the fallback before the first trade of the day, when
    the others are absent rather than wrong.
    """
    price = _to_decimal((snapshot.get("latestTrade") or {}).get("p"))
    if price:
        return price
    for section in ("minuteBar", "dailyBar", "prevDailyBar"):
        price = _to_decimal((snapshot.get(section) or {}).get("c"))
        if price:
            return price
    return None


def _previous_close(snapshot):
    """The prior session's close.

    prevDailyBar is the previous *session*, which is what "today's change"
    is measured from. dailyBar is today and must never stand in for it:
    comparing today's close against itself would report every stock as
    perfectly flat.
    """
    return _to_decimal((snapshot.get("prevDailyBar") or {}).get("c"))


def get_live_price(symbol, fresh=False):
    """Current price as a Decimal, or None if unavailable."""
    return get_quote(symbol, fresh=fresh)[0]


def get_company_name(symbol):
    return get_quote(symbol)[1]


def get_previous_close(symbol, fresh=False):
    """The previous session's close, or None if Alpaca did not report one."""
    return get_quote(symbol, fresh=fresh)[2]


# ----------------------------------------------------------------- history

# How far back each named period reaches, in days. None means "as far as
# there is".
PERIODS = {
    "1d": 1, "5d": 7, "1mo": 31, "3mo": 93, "6mo": 186,
    "1y": 366, "2y": 731, "5y": 1827, "max": None,
}

# The app's interval names, in Alpaca's spelling.
TIMEFRAMES = {
    "1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min",
    "1h": "1Hour", "1d": "1Day", "1day": "1Day",
}

# Alpaca caps a page at 10,000 bars and paginates beyond that. An all-time
# daily history is a few thousand rows, so this bounds a runaway loop
# rather than a legitimate one.
_MAX_PAGES = 20


# How far back a *session-based* range has to reach to be sure of catching
# the last one that traded.
#
# This is the fetch-side half of a constraint the read side already
# documents: see history.get_intraday_sessions, which says "1 day" has to
# mean the latest trading session, not the last 24 hours, because a
# wall-clock window returns nothing all weekend, on holidays, and before
# the open. That fix was applied to the query that *reads* stored bars —
# but if the fetch window is wall-clock, there is nothing stored to read,
# and the chart is empty for exactly the same reason.
#
# So the fetch deliberately over-reaches and lets the session SQL pick the
# day. Seven days clears a normal weekend and a Monday or Friday holiday;
# fourteen clears the longest stretch the US market closes for. Costs one
# request either way, and price_intraday is UNIQUE (symbol, ts), so the
# extra days are discarded on insert rather than duplicated.
INTRADAY_LOOKBACK_DAYS = {"1d": 7, "5d": 14}

# The earliest bar Alpaca serves on the free IEX feed, measured rather than
# assumed: a "max" request for AAPL returns nothing before 2020-07-27.
# Worth stating because "All time" on a chart means *this*, not the
# company's life on the market, and the UI has to say so.
EARLIEST_AVAILABLE = date(2020, 7, 27)


def _start_for(period, intraday=False):
    if intraday:
        days = INTRADAY_LOOKBACK_DAYS.get(period)
        if days is not None:
            return datetime.now(timezone.utc) - timedelta(days=days)

    days = PERIODS.get(period, PERIODS["6mo"])
    if days is None:
        # Before the provider's own history, so "max" means everything
        # there is rather than a window we picked.
        return datetime(EARLIEST_AVAILABLE.year, 1, 1, tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timedelta(days=days)


def _bars(symbol, timeframe, start, timeout):
    """Every bar for one symbol, following pagination to the end."""
    collected = []
    page_token = None

    for _ in range(_MAX_PAGES):
        params = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 10000,
            # Split- and dividend-adjusted, so a stock split does not read
            # as an overnight collapse on the chart.
            "adjustment": "all",
            "feed": config.ALPACA_DATA_FEED,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token

        payload = _request(f"{config.ALPACA_DATA_URL}/v2/stocks/bars",
                           params=params, timeout=timeout, symbol=symbol) or {}

        # `bars` is null, not an empty object, when there is nothing.
        rows = (payload.get("bars") or {}).get(symbol) or []
        unreadable = 0
        for row in rows:
            ts = _timestamp(row.get("t"))
            if ts is None:
                unreadable += 1
                continue
            collected.append(Bar(
                ts=ts,
                open=_to_decimal(row.get("o")),
                high=_to_decimal(row.get("h")),
                low=_to_decimal(row.get("l")),
                close=_to_decimal(row.get("c")),
                volume=int(row["v"]) if row.get("v") is not None else None,
            ))

        # Bars arrived but not one of them could be read. That is a
        # parsing failure — a changed timestamp format, most likely — and
        # it must not leave by the same door as "this range had no
        # trading". Dropping them silently would report "0 new days"
        # during a total outage of our own making, which is precisely the
        # failure this module was hardened against when it used yfinance.
        if rows and unreadable == len(rows):
            log.error(
                "Alpaca returned %s %s bars for %s and none could be parsed; "
                "the timestamp format may have changed. First: %r",
                len(rows), timeframe, symbol, rows[0].get("t"),
            )
            raise MarketDataUnavailable(symbol)
        if unreadable:
            log.warning("Skipped %s unreadable %s bars for %s of %s",
                        unreadable, timeframe, symbol, len(rows))

        page_token = payload.get("next_page_token")
        if not page_token:
            break
    else:
        log.warning("Stopped paginating %s bars for %s after %s pages",
                    timeframe, symbol, _MAX_PAGES)

    return collected


def _history(symbol, timeframe, period, timeout, intraday=False):
    """Bars, telling "Alpaca is down" apart from "no prices".

    Anything that is not a clean answer — a timeout, rejected credentials,
    the rate limit, a 5xx — leaves _request as an exception, so an outage
    can never be mistaken for a quiet day and reported as "0 new days".
    An empty list here means Alpaca answered and had nothing.
    """
    symbol = (symbol or "").strip().upper()
    if resolve_symbol(symbol) is None:
        log.info("Alpaca does not list the symbol %s", symbol)
        raise UnknownSymbol(symbol)

    bars = _bars(symbol, timeframe, _start_for(period, intraday=intraday), timeout)
    if not bars:
        log.info("Alpaca has no %s bars for %s over %s", timeframe, symbol, period)
    return bars


def get_price_history(symbol, period="6mo"):
    """Daily OHLCV bars, oldest first."""
    return _history(symbol, "1Day", period, config.MARKET_HISTORY_TIMEOUT)


def get_intraday(symbol, period="1d", interval="5m"):
    """Intraday bars for the short-range charts.

    `period` names a number of *trading sessions*, not a span of hours,
    and the window fetched is deliberately wider than it — see
    INTRADAY_LOOKBACK_DAYS. Asking for literally the last 24 hours returns
    nothing from Friday evening until Monday's open.
    """
    timeframe = TIMEFRAMES.get(interval, "5Min")
    return _history(symbol, timeframe, period, config.MARKET_HISTORY_TIMEOUT,
                    intraday=True)


# ------------------------------------------------------------- the search

# The whole tradable catalogue, for searching by company name. Fetched in
# one request and held for a day, like the per-symbol entries above and
# for the same reason: a listing changes with corporate actions, not with
# the minute.
#
# Note this is a *different* cache from _assets. That one is filled lazily,
# one symbol at a time, by lookups that already know the ticker — it can
# never answer "what is Apple's symbol", because a symbol nobody has
# looked up yet is not in it.
_CATALOGUE_SECONDS = 24 * 60 * 60
_catalogue = {"expires": 0.0, "assets": []}
_catalogue_lock = threading.Lock()

# Trailing words that describe how a security is listed rather than what
# it is. Stripped for display only.
#
# What is deliberately NOT here matters more: Class, Series, ETF, Fund,
# Trust, Warrant, Right and Unit all distinguish one instrument from
# another. Stripping "Class B" would render BRK.A and BRK.B identically,
# which is the one outcome this feature must not produce.
_LISTING_SUFFIXES = (
    "common stock", "common shares", "ordinary shares",
    "american depositary shares", "american depositary receipt",
)


def display_name(name):
    """A company name with listing boilerplate trimmed off the end.

    Display only. Matching always runs against the raw name as well, so
    trimming can never make a company unfindable, and nothing is ever
    merged: two assets that trim to the same text remain two results,
    told apart by the symbol, which is always shown.
    """
    if not name:
        return name
    trimmed = name.strip()
    lowered = trimmed.lower()
    for suffix in _LISTING_SUFFIXES:
        if lowered.endswith(suffix) and len(trimmed) > len(suffix) + 1:
            return trimmed[: -len(suffix)].strip(" -,")
    return trimmed


def catalogue(fresh=False):
    """Every tradable US equity Alpaca lists: [{symbol, name}, ...]."""
    if not fresh:
        with _catalogue_lock:
            if _catalogue["expires"] > time.monotonic() and _catalogue["assets"]:
                return _catalogue["assets"]

    rows = _request(
        f"{config.ALPACA_TRADING_URL}/v2/assets",
        params={"status": "active", "asset_class": "us_equity"},
        timeout=config.MARKET_HISTORY_TIMEOUT,
        symbol="catalogue",
    ) or []

    # Only what the search needs. The full payload is ~6 MB; this is ~1 MB,
    # and the rest would be held for a day for nothing.
    assets = [
        {"symbol": row["symbol"], "name": row.get("name") or row["symbol"]}
        for row in rows
        if row.get("tradable") and row.get("symbol")
    ]
    with _catalogue_lock:
        _catalogue["assets"] = assets
        _catalogue["expires"] = time.monotonic() + _CATALOGUE_SECONDS
    log.info("Loaded %s tradable assets into the search catalogue", len(assets))
    return assets


def _word_starts(haystack, needle):
    """True when some word in `haystack` begins with `needle`."""
    for part in re.split(r"[^a-z0-9]+", haystack):
        if part.startswith(needle):
            return True
    return False


def search_assets(query, limit=8):
    """Find tradable assets by ticker or company name, best match first.

    Ranking is the substance of this, not a refinement. A plain substring
    search for "apple" returns Maui Land & Pineapple, Pineapple Financial
    and two leveraged Apple ETFs before Apple itself — which for someone
    who does not yet know that Apple is AAPL is worse than no search,
    because the plausible-looking answer is a 2x derivative.

    So matches are tiered, and only sorted within a tier:

        0  the symbol, exactly
        1  the symbol starts with it
        2  the name starts with it          <- "Apple Inc." for "apple"
        3  some word in the name starts with it
        4  it appears anywhere in the name

    "Pineapple" has no word starting with "apple", so it can only reach
    tier 4. Nothing is filtered or penalised by what kind of security it
    is: the tiers sink derivative products on their own, and a hand-made
    blocklist would be this app deciding what you are allowed to find.
    """
    needle = (query or "").strip().lower()
    if len(needle) < 1:
        return []

    try:
        assets = catalogue()
    except MarketDataUnavailable:
        # Search is an aid, not the mechanism: a typed ticker still works.
        log.warning("Asset catalogue unavailable; search returning nothing")
        return []

    scored = []
    for asset in assets:
        symbol = asset["symbol"]
        symbol_lower = symbol.lower()
        raw = asset["name"]
        shown = display_name(raw)
        # Matched against both, so trimming for display can never hide a
        # company whose boilerplate the reader actually typed.
        hay = f"{raw}\n{shown}".lower()

        if symbol_lower == needle:
            tier = 0
        elif symbol_lower.startswith(needle):
            tier = 1
        elif hay.startswith(needle) or shown.lower().startswith(needle):
            tier = 2
        elif _word_starts(hay, needle):
            tier = 3
        elif needle in hay or needle in symbol_lower:
            tier = 4
        else:
            continue

        # Within a tier, the shorter name is the plainer one: "Apple Inc."
        # before "Apple Hospitality REIT", "Vanguard S&P 500 ETF" before
        # "Vanguard S&P Mid-Cap 400 Growth ETF".
        scored.append((tier, len(shown), symbol, shown))
        if tier == 0 and len(scored) > limit * 4:
            break

    scored.sort(key=lambda row: (row[0], row[1], row[2]))
    return [{"symbol": s, "name": n} for _, _, s, n in scored[:limit]]

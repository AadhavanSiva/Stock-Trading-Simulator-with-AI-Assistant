"""Shared test fixtures.

Database tests run against a throwaway database (default: portfolio_test),
never the app's real one. If PostgreSQL isn't reachable the DB tests skip
instead of failing, so the pure-logic tests still run anywhere. Set
REQUIRE_DB=1 (CI does) to make an unreachable database a failure instead,
so a broken service container cannot pass as a green run of skips.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

# The app refuses to start without a signing key outside debug mode. Tests
# are not a debug run, so give them a throwaway key before anything imports
# the config. A key already in the environment is left alone.
os.environ.setdefault("FLASK_SECRET_KEY", "test-only-key-not-a-secret")

from portfolio_tracker import config  # noqa: E402

TEST_DB_NAME = os.getenv("TEST_DB_NAME", "portfolio_test")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")


def _admin_connection():
    import psycopg2

    return psycopg2.connect(
        host=config.DB_HOST,
        database="postgres",
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        port=config.DB_PORT,
    )


@pytest.fixture(autouse=True)
def isolated_auth_config(monkeypatch):
    """Pin the auth settings so tests never depend on the local .env.

    Once real GOOGLE_CLIENT_ID/SECRET values exist on a machine, anything
    asserting on the unconfigured state starts failing there and passing
    in CI, or the other way round. Every test starts from "no Google, no
    dev login"; the ones that need credentials patch them in themselves.
    """
    # Market data credentials are pinned for the same reason as the sign-in
    # ones below: once real Alpaca keys exist on a machine, a test that
    # forgot to stub the transport would reach the live API from there and
    # not from CI. These are obvious fakes, and the session guard means no
    # request is made with them anyway.
    monkeypatch.setattr(config, "ALPACA_API_KEY_ID", "test-key-id-not-a-secret")
    monkeypatch.setattr(config, "ALPACA_API_SECRET_KEY", "test-secret-not-a-secret")
    monkeypatch.setattr(config, "ALPACA_DATA_FEED", "iex")
    monkeypatch.setattr(config, "ALPACA_DATA_URL", "https://data.alpaca.test")
    monkeypatch.setattr(config, "ALPACA_TRADING_URL", "https://api.alpaca.test")

    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", None)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", None)
    monkeypatch.setattr(config, "ALLOW_DEV_LOGIN", False)
    monkeypatch.setattr(config, "PORTFOLIO_USER", None)
    monkeypatch.setattr(config, "PORTFOLIO_USER_ID", None)
    # A DATABASE_URL in .env points at a real (possibly hosted) database;
    # the app would connect there instead of the throwaway test database.
    monkeypatch.setattr(config, "DATABASE_URL", None)
    # Same reasoning for the assistant: an ASSISTANT_MODEL in someone's .env
    # must not change what the tests assert.
    monkeypatch.setattr(config, "ASSISTANT_ENABLED", True)
    monkeypatch.setattr(config, "ASSISTANT_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(config, "ASSISTANT_THINKING", "medium")


class _NoRealAPICalls:
    """Stands in for the Gemini client during tests.

    A test that forgets to mock the client would otherwise reach the real
    API — using quota, and on a free key sending test data to Google —
    whenever the machine running the suite has a key configured. This
    makes that mistake fail loudly instead.
    """
    class _Models:
        def generate_content(self, **kwargs):
            raise AssertionError(
                "A test tried to call the real Gemini API. Patch "
                "portfolio_tracker.services.assistant._get_client."
            )

    def __init__(self):
        self.models = self._Models()


@pytest.fixture(autouse=True)
def no_real_api_calls(monkeypatch):
    from portfolio_tracker.services import assistant

    monkeypatch.setattr(assistant, "_client", _NoRealAPICalls())
    # A search refusal in one test must not leave search paused for the next.
    monkeypatch.setattr(assistant, "_search_blocked_until", 0.0)


class RealMarketDataCall(BaseException):
    """Raised when a test reaches the market data provider itself.

    A BaseException on purpose: market_data turns any ordinary exception
    into "the market data service didn't respond", which would let an
    unmocked test pass quietly. This one is not caught there, so the test
    fails loudly instead, and the suite stays offline in CI.
    """


def _no_real_market_data(*args, **kwargs):
    target = args[0] if args else kwargs.get("url", "the provider")
    raise RealMarketDataCall(
        f"A test tried to reach {target}. Patch market_data.get_quote / "
        "get_live_price / get_price_history / get_intraday / "
        "resolve_symbol, or market_data._session.get for a response-level "
        "double."
    )


@pytest.fixture(autouse=True)
def no_real_market_data(monkeypatch):
    """Close the network at the one place every request passes through.

    Patched on the session rather than on the named endpoints, so a call
    added later is covered without anyone remembering to extend this —
    every outbound request in market_data goes through _request, and every
    _request goes through this.
    """
    from portfolio_tracker.services import market_data

    monkeypatch.setattr(market_data._session, "get", _no_real_market_data)


@pytest.fixture(autouse=True)
def empty_quote_cache():
    """A quote cached by one test must not answer for the next."""
    from portfolio_tracker.services import market_data

    market_data.clear_quote_cache()
    yield
    market_data.clear_quote_cache()


@pytest.fixture(scope="session")
def test_database():
    """Create the test database once per run and point config at it."""
    psycopg2 = pytest.importorskip("psycopg2")

    try:
        admin = _admin_connection()
    except psycopg2.OperationalError as exc:
        if os.getenv("REQUIRE_DB", "").strip() in ("1", "true", "yes"):
            pytest.fail(f"REQUIRE_DB is set but PostgreSQL is not reachable: {exc}")
        pytest.skip(f"PostgreSQL not reachable: {exc}")

    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    admin.close()

    original = config.DB_NAME
    config.DB_NAME = TEST_DB_NAME

    conn = psycopg2.connect(
        host=config.DB_HOST,
        database=TEST_DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        port=config.DB_PORT,
    )
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS watchlist, portfolio_value_history, trades, "
                    "assistant_requests, price_intraday, price_history, portfolio, "
                    "stocks, users CASCADE")
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            cur.execute(fh.read())
    conn.commit()
    conn.close()

    yield TEST_DB_NAME

    config.DB_NAME = original


@pytest.fixture
def db(test_database):
    """Empty tables before each test so cases can't leak into each other."""
    from portfolio_tracker.db import cursor

    with cursor(commit=True) as cur:
        cur.execute(
            # TRUNCATE does not fire row-level triggers, so the append-only guard
            # on `trades` does not stand in the way of resetting between tests.
            "TRUNCATE watchlist, portfolio_value_history, trades, assistant_requests, "
            "price_intraday, price_history, portfolio, stocks, users "
            "RESTART IDENTITY CASCADE"
        )
    return test_database


@pytest.fixture
def user(db):
    """A signed-in account with the standard starting cash."""
    from portfolio_tracker.models import users

    account = users.upsert_from_google("test-sub-1", "tester@example.com", "Tester")
    return account[0]


@pytest.fixture
def other_user(db):
    """A second account, for proving one user cannot see another's holdings."""
    from portfolio_tracker.models import users

    account = users.upsert_from_google("test-sub-2", "other@example.com", "Other")
    return account[0]


@pytest.fixture
def seeded(user):
    """Stocks that holdings can reference (portfolio.symbol has an FK)."""
    from portfolio_tracker.models import stocks

    stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("200.00"))
    stocks.upsert_stock("MSFT", "Microsoft", Decimal("400.00"))
    return user


@pytest.fixture
def anon(db, monkeypatch):
    """A web client with no session — signed out.

    CSRF checks are off for these general-purpose clients so each test can
    post a form directly. tests/test_csrf.py turns them back on and proves
    every state-changing route refuses a request without a valid token,
    and that every rendered form carries one.
    """
    import web as web_module

    web_module.app.config.update(TESTING=True)
    monkeypatch.setitem(web_module.app.config, "WTF_CSRF_ENABLED", False)
    with web_module.app.test_client() as c:
        yield c


@pytest.fixture
def client(user, monkeypatch):
    """A web client already signed in as the `user` fixture's account."""
    import web as web_module

    web_module.app.config.update(TESTING=True)
    monkeypatch.setitem(web_module.app.config, "WTF_CSRF_ENABLED", False)
    with web_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = user
        yield c


def rows(table="portfolio"):
    """Raw table contents, for asserting on what actually landed on disk."""
    from portfolio_tracker.db import cursor

    with cursor() as cur:
        cur.execute(f"SELECT * FROM {table} ORDER BY 1")
        return cur.fetchall()


# ---------------------------------------------------------- market data doubles
# Alpaca answers JSON over HTTP, so a double is a canned response rather
# than a fabricated DataFrame. These live here because six test modules
# need the same two shapes.

class FakeResponse:
    """Enough of requests.Response for market_data._request."""

    def __init__(self, status_code=200, payload=None, text=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def asset_payload(symbol="AAPL", name="Apple Inc.", tradable=True,
                  status="active", asset_class="us_equity"):
    """One row of Alpaca's asset catalogue."""
    return {"symbol": symbol, "name": name, "tradable": tradable,
            "status": status, "class": asset_class, "exchange": "NASDAQ"}


def snapshot_payload(price="100", previous_close="90", symbol="AAPL"):
    """An Alpaca snapshot, in the shape the real endpoint returns."""
    payload = {"symbol": symbol}
    if price is not None:
        payload["latestTrade"] = {"p": float(price), "t": "2026-09-18T19:59:00Z"}
    if previous_close is not None:
        payload["prevDailyBar"] = {"c": float(previous_close),
                                   "t": "2026-09-17T04:00:00Z"}
    return payload


def bars_payload(symbol="AAPL", closes=(100.0,), start="2026-09-15T04:00:00Z",
                 next_page_token=None):
    """A bars response. `closes=None` produces the null Alpaca really sends."""
    if closes is None:
        return {"bars": None, "symbol": symbol, "next_page_token": None}
    begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
    return {
        "bars": {symbol: [
            {"t": (begin + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "o": c, "h": c, "l": c, "c": c, "v": 1000 + i, "n": 10, "vw": c}
            for i, c in enumerate(closes)
        ]},
        "next_page_token": next_page_token,
    }


def bars(closes, start=None, minutes=None, symbol="AAPL"):
    """market_data.Bar objects, for doubling get_price_history/get_intraday.

    `minutes` spaces the bars by minutes rather than days, for intraday.
    """
    from portfolio_tracker.services.market_data import Bar

    begin = start or (datetime.now(timezone.utc) - timedelta(days=len(closes)))
    step = timedelta(minutes=minutes) if minutes else timedelta(days=1)
    made = []
    for i, close in enumerate(closes):
        value = Decimal(str(close))
        made.append(Bar(ts=begin + step * i, open=value, high=value,
                        low=value, close=value, volume=1000 + i))
    return made


def route_alpaca(asset=None, snapshot=None, bars_response=None, status=200):
    """Patch the HTTP session, routing by URL to canned responses.

    Patching the transport rather than the named functions is what lets a
    test exercise the parsing and the error handling that sit between
    them — which is where the interesting behaviour now lives.
    """
    from unittest.mock import patch

    from portfolio_tracker.services import market_data

    def get(url, **kwargs):
        if "/v2/assets/" in url:
            if asset is None:
                return FakeResponse(404, text="asset not found")
            return FakeResponse(status, asset)
        if "/snapshot" in url:
            return FakeResponse(status, snapshot if snapshot is not None else {})
        return FakeResponse(status, bars_response
                            if bars_response is not None else {"bars": None})

    return patch.object(market_data._session, "get", side_effect=get)

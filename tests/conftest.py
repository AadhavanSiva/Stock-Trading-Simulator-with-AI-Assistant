"""Shared test fixtures.

Database tests run against a throwaway database (default: portfolio_test),
never the app's real one. If PostgreSQL isn't reachable the DB tests skip
instead of failing, so the pure-logic tests still run anywhere. Set
REQUIRE_DB=1 (CI does) to make an unreachable database a failure instead,
so a broken service container cannot pass as a green run of skips.
"""
import os
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
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", None)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", None)
    monkeypatch.setattr(config, "ALLOW_DEV_LOGIN", False)
    monkeypatch.setattr(config, "PORTFOLIO_USER", None)
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
    """Raised when a test reaches yfinance itself.

    A BaseException on purpose: market_data turns any ordinary exception
    from yfinance into "Yahoo Finance didn't respond", which would let an
    unmocked test pass quietly. This one is not caught there, so the test
    fails loudly instead, and the suite stays offline in CI.
    """


class _NoRealMarketData:
    def __init__(self, symbol, *args, **kwargs):
        raise RealMarketDataCall(
            f"A test tried to call Yahoo Finance for {symbol!r}. Patch "
            "market_data.get_quote / get_live_price / get_price_history, "
            "or market_data.yf.Ticker."
        )


@pytest.fixture(autouse=True)
def no_real_market_data(monkeypatch):
    from portfolio_tracker.services import market_data

    monkeypatch.setattr(market_data.yf, "Ticker", _NoRealMarketData)


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
        cur.execute("DROP TABLE IF EXISTS assistant_requests, price_intraday, price_history, "
                    "portfolio, stocks, users CASCADE")
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
            "TRUNCATE assistant_requests, price_intraday, price_history, portfolio, stocks, users "
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

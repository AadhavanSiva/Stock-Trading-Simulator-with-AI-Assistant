"""Shared test fixtures.

Database tests run against a throwaway database (default: portfolio_test),
never the app's real one. If PostgreSQL isn't reachable the DB tests skip
instead of failing, so the pure-logic tests still run anywhere.
"""
import os
from decimal import Decimal

import pytest

from portfolio_tracker import config

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


@pytest.fixture(scope="session")
def test_database():
    """Create the test database once per run and point config at it."""
    psycopg2 = pytest.importorskip("psycopg2")

    try:
        admin = _admin_connection()
    except psycopg2.OperationalError as exc:
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
        cur.execute("DROP TABLE IF EXISTS price_intraday, price_history, portfolio, stocks, users CASCADE")
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
            "TRUNCATE price_intraday, price_history, portfolio, stocks, users RESTART IDENTITY CASCADE"
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
def anon(db):
    """A web client with no session — signed out."""
    import web as web_module

    web_module.app.config.update(TESTING=True)
    with web_module.app.test_client() as c:
        yield c


@pytest.fixture
def client(user):
    """A web client already signed in as the `user` fixture's account."""
    import web as web_module

    web_module.app.config.update(TESTING=True)
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

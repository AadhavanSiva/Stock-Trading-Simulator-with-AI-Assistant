"""A database is a database, however it got here.

There are two routes in. A fresh deploy runs schema.sql. An existing
database gets the files in migrations/. If those two disagree, the bugs
that follow only ever appear in one of them — which is the worst place for
a bug to live, because the environment that reproduces it is the one you
cannot experiment on.

These build both, in throwaway databases, and compare the result down to
the constraints.
"""
import os

import psycopg2
import pytest

from portfolio_tracker import config, init_db

FRESH = "portfolio_parity_fresh"
MIGRATED = "portfolio_parity_migrated"


def admin():
    conn = psycopg2.connect(
        host=config.DB_HOST, database="postgres", user=config.DB_USER,
        password=config.DB_PASSWORD, port=config.DB_PORT,
    )
    conn.autocommit = True
    return conn


def run_on_admin(*statements):
    """psycopg2's `with conn:` opens a transaction block, and DROP DATABASE
    cannot run inside one — so this drives the cursor directly."""
    conn = admin()
    try:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
    finally:
        conn.close()


def recreate(name):
    run_on_admin(f'DROP DATABASE IF EXISTS "{name}"', f'CREATE DATABASE "{name}"')


def connect(name):
    conn = psycopg2.connect(
        host=config.DB_HOST, database=name, user=config.DB_USER,
        password=config.DB_PASSWORD, port=config.DB_PORT,
    )
    conn.autocommit = True
    return conn


def run_sql(name, path):
    conn = connect(name)
    try:
        with conn.cursor() as cur, open(path, encoding="utf-8") as fh:
            cur.execute(fh.read())
    finally:
        conn.close()


def describe(name):
    """The shape of a database, as comparable values."""
    conn = connect(name)
    try:
      with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, column_name
        """)
        columns = cur.fetchall()

        # Constraint *definitions*, not names: two routes may name an
        # anonymous constraint differently while meaning the same thing,
        # and it is the meaning that has to match.
        cur.execute("""
            SELECT rel.relname, pg_get_constraintdef(con.oid)
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace ns ON ns.oid = rel.relnamespace
            WHERE ns.nspname = 'public'
            ORDER BY rel.relname, pg_get_constraintdef(con.oid)
        """)
        constraints = cur.fetchall()

        cur.execute("""
            SELECT tablename, indexdef FROM pg_indexes
            WHERE schemaname = 'public' ORDER BY tablename, indexdef
        """)
        indexes = cur.fetchall()

        cur.execute("""
            SELECT rel.relname, tg.tgname, pg_get_triggerdef(tg.oid)
            FROM pg_trigger tg
            JOIN pg_class rel ON rel.oid = tg.tgrelid
            JOIN pg_namespace ns ON ns.oid = rel.relnamespace
            WHERE ns.nspname = 'public' AND NOT tg.tgisinternal
            ORDER BY rel.relname, tg.tgname
        """)
        triggers = cur.fetchall()

    finally:
        conn.close()

    return {"columns": columns, "constraints": constraints,
            "indexes": indexes, "triggers": triggers}


@pytest.fixture(scope="module")
def both_databases(test_database):
    """Build one database each way, and hand back both descriptions."""
    try:
        admin().close()
    except psycopg2.OperationalError as exc:            # pragma: no cover
        pytest.skip(f"PostgreSQL not reachable: {exc}")

    recreate(FRESH)
    run_sql(FRESH, init_db.SCHEMA_PATH)

    recreate(MIGRATED)
    run_sql(MIGRATED, init_db.SCHEMA_PATH)
    for path in init_db.migration_files():
        run_sql(MIGRATED, path)

    yield describe(FRESH), describe(MIGRATED)

    run_on_admin(*(f'DROP DATABASE IF EXISTS "{n}"' for n in (FRESH, MIGRATED)))


class TestTheTwoRoutesConverge:
    def test_the_same_tables_and_columns(self, both_databases):
        fresh, migrated = both_databases
        assert fresh["columns"] == migrated["columns"]

    def test_the_same_constraints(self, both_databases):
        """The one that would have caught price_history: it had no CHECKs
        while every comparable table did."""
        fresh, migrated = both_databases
        assert fresh["constraints"] == migrated["constraints"]

    def test_the_same_indexes(self, both_databases):
        fresh, migrated = both_databases
        assert fresh["indexes"] == migrated["indexes"]

    def test_the_same_triggers(self, both_databases):
        """Including the append-only guard on `trades`."""
        fresh, migrated = both_databases
        assert fresh["triggers"] == migrated["triggers"]
        assert any("trades" in row[0] for row in fresh["triggers"])


class TestMigrationsAreSafeOnACurrentDatabase:
    def test_running_them_all_again_changes_nothing(self, both_databases):
        """They have to be no-ops once applied, or init_db cannot run them
        on every deploy."""
        before = describe(MIGRATED)
        for path in init_db.migration_files():
            run_sql(MIGRATED, path)
        assert describe(MIGRATED) == before

    def test_they_run_against_a_schema_that_already_has_everything(self, both_databases):
        """Applied to a database schema.sql just built, each must still
        succeed rather than trip over what is already there."""
        for path in init_db.migration_files():
            run_sql(FRESH, path)
        assert describe(FRESH) == describe(MIGRATED)


class TestInitDbAppliesBoth:
    def test_it_knows_about_every_migration(self):
        """A migration added but not picked up would break the invariant
        quietly, on hosted databases only."""
        names = [os.path.basename(p) for p in init_db.migration_files()]
        on_disk = sorted(
            f for f in os.listdir(os.path.dirname(init_db.MIGRATIONS_DIR + os.sep))
            if f.endswith(".sql")
        )
        assert names == on_disk

    def test_they_are_applied_in_numeric_order(self):
        names = [os.path.basename(p) for p in init_db.migration_files()]
        assert names == sorted(names)
        assert names[0].startswith("001")

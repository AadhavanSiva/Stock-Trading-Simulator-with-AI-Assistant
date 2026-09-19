"""Bring the configured database up to date.

Usage:  python -m portfolio_tracker.init_db

Applies schema.sql and then every file in migrations/, in order, to
whatever database the config points at: DATABASE_URL when set (a hosted
database such as Neon), otherwise the local DB_* settings.

Both halves are needed, and neither is enough alone:

  * schema.sql is written entirely in CREATE ... IF NOT EXISTS, so it
    builds a new database but deliberately never alters an existing one.
    A database created before a column gained a constraint would keep the
    old shape forever, however often this ran.
  * The migrations do alter existing tables, and are each written to be
    safe to re-run — so on a database that is already current they find
    nothing to do.

Running both is what makes the two routes into a database converge: a
fresh deploy and an upgraded one end up with the same schema and
consistent data, rather than differing by whatever was added after the
older one was created. tests/test_schema_parity.py holds that to account.

This exists so a hosted database can be set up without psql, and so a
deploy can run it as part of the build.
"""
import glob
import os
import sys

from portfolio_tracker import config, db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "schema.sql")
MIGRATIONS_DIR = os.path.join(ROOT, "migrations")


def migration_files():
    """Every migration, in the order their numeric prefixes imply."""
    return sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))


def apply_schema():
    """Run schema.sql in one transaction; nothing is left half-created."""
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        sql = fh.read()
    with db.cursor(commit=True) as cur:
        cur.execute(sql)
        cur.execute("SELECT current_database()")
        return cur.fetchone()[0]


def apply_migrations():
    """Run each migration in its own transaction, oldest first.

    One transaction each rather than one for all of them: a migration that
    fails should leave the ones before it applied, so re-running resumes
    rather than starting over. Each file manages its own BEGIN/COMMIT, so
    this hands them a connection in autocommit and lets them do it.
    """
    applied = []
    conn = db.get_connection()
    conn.autocommit = True
    try:
        for path in migration_files():
            with open(path, encoding="utf-8") as fh:
                sql = fh.read()
            with conn.cursor() as cur:
                cur.execute(sql)
            applied.append(os.path.basename(path))
    finally:
        conn.close()
    return applied


def main():
    source = "DATABASE_URL" if config.DATABASE_URL else "DB_* settings"
    try:
        name = apply_schema()
    except Exception as exc:
        print(f"Could not apply schema.sql (using {source}): {exc}", file=sys.stderr)
        return 1
    print(f"schema.sql applied to database '{name}' (using {source}).")

    try:
        applied = apply_migrations()
    except Exception as exc:
        print(f"Could not apply migrations: {exc}", file=sys.stderr)
        return 1
    print(f"migrations applied ({len(applied)}): {', '.join(applied) or 'none found'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

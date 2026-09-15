"""Create the app's tables in the configured database.

Usage:  python -m portfolio_tracker.init_db

Applies schema.sql to whatever database the config points at: DATABASE_URL
when set (a hosted database such as Neon), otherwise the local DB_* settings.
Every statement in schema.sql is CREATE ... IF NOT EXISTS, so running this
again is harmless. It does not alter tables that already exist; an older
database still needs the files in migrations/.

This exists so a fresh hosted database can be set up without psql, and so a
deploy can run it as part of the build.
"""
import os
import sys

from portfolio_tracker import config, db

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")


def apply_schema():
    """Run schema.sql in one transaction; nothing is left half-created."""
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        sql = fh.read()
    with db.cursor(commit=True) as cur:
        cur.execute(sql)
        cur.execute("SELECT current_database()")
        return cur.fetchone()[0]


def main():
    source = "DATABASE_URL" if config.DATABASE_URL else "DB_* settings"
    try:
        name = apply_schema()
    except Exception as exc:
        print(f"Could not apply schema.sql (using {source}): {exc}", file=sys.stderr)
        return 1
    print(f"schema.sql applied to database '{name}' (using {source}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import contextlib

import psycopg2

from portfolio_tracker import config


def get_connection():
    if config.DATABASE_URL:
        # Passed whole, so options such as ?sslmode=require still apply.
        return psycopg2.connect(config.DATABASE_URL)
    return psycopg2.connect(
        host=config.DB_HOST,
        database=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        port=config.DB_PORT
    )


@contextlib.contextmanager
def cursor(commit=False):
    """Yield a cursor and always close the connection behind it.

    Commits on a clean exit when commit=True; rolls back and re-raises
    if the block fails, so a half-finished write is never left behind.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
            if commit:
                conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

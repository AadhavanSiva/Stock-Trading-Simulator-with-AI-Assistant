"""The assistant's per-account rate limit, counted in the database.

Kept in PostgreSQL rather than process memory so the limit survives a
restart and is one shared limit however many worker processes serve the app.
"""
import math

from portfolio_tracker.db import cursor

# First key for pg_advisory_xact_lock, so this lock cannot collide with any
# other advisory lock taken on the same account id.
_LOCK_NAMESPACE = 5001


def reserve_question(user_id, limit, window_seconds):
    """Record one question if the account is under its limit.

    Returns 0 when the question is allowed (it has been recorded), or else
    how many whole seconds remain until the oldest question in the window
    expires and another is allowed.

    The check and the insert happen under a transaction-scoped advisory
    lock on the account. Without it, two requests arriving together, in
    the same process or different ones, could both count limit - 1 and
    both be let through.
    """
    if limit <= 0:
        return max(1, int(window_seconds))

    with cursor(commit=True) as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK_NAMESPACE, user_id))
        # Expired rows are cleared as we go, so the table holds at most
        # `limit` rows per account.
        cur.execute(
            """
            DELETE FROM assistant_requests
            WHERE user_id = %s
              AND asked_at <= now() - make_interval(secs => %s)
            """,
            (user_id, window_seconds),
        )
        cur.execute(
            """
            SELECT count(*),
                   EXTRACT(EPOCH FROM min(asked_at) + make_interval(secs => %s) - now())
            FROM assistant_requests
            WHERE user_id = %s
            """,
            (window_seconds, user_id),
        )
        count, seconds_left = cur.fetchone()
        if count >= limit:
            return max(1, math.ceil(seconds_left))

        cur.execute("INSERT INTO assistant_requests (user_id) VALUES (%s)", (user_id,))
        return 0

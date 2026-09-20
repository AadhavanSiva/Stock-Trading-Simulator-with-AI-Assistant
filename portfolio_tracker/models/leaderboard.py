"""Standings, read from the value samples rather than recomputed.

Ranking accounts means knowing what each is worth, and working that out
from `portfolio` would mean re-pricing every holding of every account on
every page view. `portfolio_value_history` already holds that figure as of
the last refresh, so this reads the newest sample per account and sorts.
The leaderboard is therefore exactly as current as the last price refresh,
which is the honest thing for it to be.

What is never selected here matters as much as what is: no email, no
Google display name, no holdings, no trades, no cash balance. The columns
this module can return are the ones the page is allowed to show.
"""
from portfolio_tracker.db import cursor

# The latest sample per account, as a reusable subquery. DISTINCT ON walks
# portfolio_value_history_user_time_idx and takes the first row per user.
_LATEST = """
    SELECT DISTINCT ON (user_id) user_id, total_value, recorded_at
    FROM portfolio_value_history
    ORDER BY user_id, recorded_at DESC, id DESC
"""


def standings(limit=50):
    """The opted-in accounts, best first.

    Returns (rank, user_id, leaderboard_name, total_value, recorded_at).
    `user_id` is for the caller to spot the viewer's own row; it is not
    for display and identifies nobody outside this application.

    Ties share a rank — RANK() rather than ROW_NUMBER() — because two
    accounts worth the same amount are level, and breaking that by id
    would invent an ordering out of whichever account was created first.
    """
    with cursor() as cur:
        cur.execute(
            f"""
            SELECT RANK() OVER (ORDER BY latest.total_value DESC) AS position,
                   u.id, u.leaderboard_name, latest.total_value, latest.recorded_at
            FROM users u
            JOIN ({_LATEST}) latest ON latest.user_id = u.id
            WHERE u.leaderboard_opt_in
            ORDER BY latest.total_value DESC, u.id
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def standing_for(user_id):
    """Where one account stands, whether or not it has opted in.

    Returns (rank, total_value, recorded_at, opted_in, participants), or
    None when the account has never been valued.

    A rank is computed for everyone: someone deciding whether to join is
    entitled to know what joining would show, and refusing to tell them
    until they opt in would make the choice uninformed. The rank counts
    only opted-in accounts, so it is the position they would occupy — a
    private account is not silently occupying a slot in the public list.
    """
    with cursor() as cur:
        cur.execute(
            f"""
            WITH latest AS ({_LATEST}),
            me AS (
                SELECT u.id, u.leaderboard_opt_in, latest.total_value, latest.recorded_at
                FROM users u
                JOIN latest ON latest.user_id = u.id
                WHERE u.id = %s
            )
            SELECT (
                       SELECT count(*) + 1
                       FROM users other
                       JOIN latest ON latest.user_id = other.id
                       WHERE other.leaderboard_opt_in
                         AND other.id <> me.id
                         AND latest.total_value > me.total_value
                   ) AS position,
                   me.total_value,
                   me.recorded_at,
                   me.leaderboard_opt_in,
                   (
                       SELECT count(*)
                       FROM users p
                       JOIN latest ON latest.user_id = p.id
                       WHERE p.leaderboard_opt_in
                   ) AS participants
            FROM me
            """,
            (user_id,),
        )
        return cur.fetchone()


def participant_count():
    with cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*)
            FROM users u
            JOIN ({_LATEST}) latest ON latest.user_id = u.id
            WHERE u.leaderboard_opt_in
            """
        )
        return cur.fetchone()[0]


def name_taken(name, excluding_user_id=None):
    """Whether another account already shows that name.

    Not a database UNIQUE: two people may reasonably want the same
    nickname, and a constraint would turn that into a failed save with no
    good recovery. This lets the form say so and suggest another.
    """
    sql = "SELECT 1 FROM users WHERE lower(btrim(leaderboard_name)) = lower(btrim(%s))"
    params = [name]
    if excluding_user_id is not None:
        sql += " AND id <> %s"
        params.append(excluding_user_id)
    with cursor() as cur:
        cur.execute(sql + " LIMIT 1", tuple(params))
        return cur.fetchone() is not None

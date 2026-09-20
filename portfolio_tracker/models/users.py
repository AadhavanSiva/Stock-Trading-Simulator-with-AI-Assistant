"""Accounts and their cash balances."""
import psycopg2
from decimal import Decimal

from portfolio_tracker import config
from portfolio_tracker.db import cursor
from portfolio_tracker.errors import UnknownUser, ValidationError


def starting_cash():
    return Decimal(str(config.STARTING_CASH))


def upsert_from_google(google_sub, email, display_name=None):
    """Find or create the account behind a Google sign-in.

    Keyed on Google's `sub` claim rather than the email address, because
    people change their email and `sub` is stable for the life of the
    account. Email and name are refreshed on each sign-in.

    Returns the account row.
    """
    with cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO users (google_sub, email, display_name, cash)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (google_sub) DO UPDATE SET
                email        = EXCLUDED.email,
                display_name = COALESCE(EXCLUDED.display_name, users.display_name)
            RETURNING id, google_sub, email, display_name, cash
            """,
            (google_sub, email, display_name, starting_cash()),
        )
        return cur.fetchone()


def get_by_id(user_id):
    with cursor() as cur:
        cur.execute(
            "SELECT id, google_sub, email, display_name, cash FROM users WHERE id = %s",
            (user_id,),
        )
        return cur.fetchone()


def get_by_google_sub(google_sub):
    """Look up an account by Google's subject claim.

    Used to tell a first-ever sign-in from a returning one, so the welcome
    can be honest about which it is. Google itself draws no distinction
    between signing up and logging in — the first sign-in is the sign-up.
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT id, google_sub, email, display_name, cash
            FROM users WHERE google_sub = %s
            """,
            (google_sub,),
        )
        return cur.fetchone()


def get_by_email(email):
    """The account at an address — the oldest one, if more than one shares it.

    `email` is not unique and deliberately so (see list_by_email), so this
    orders before it takes one row. Without the ORDER BY, PostgreSQL is
    free to return either row and may return a different one from one call
    to the next, which made `PORTFOLIO_USER` resolve to an account at
    random. Callers that must not guess should use list_by_email instead.
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT id, google_sub, email, display_name, cash
            FROM users WHERE lower(email) = lower(%s)
            ORDER BY id
            """,
            (email,),
        )
        return cur.fetchone()


def list_by_email(email):
    """Every account at an address, oldest first.

    More than one is possible. `google_sub` is the identity; the email is a
    mutable attribute refreshed from Google on each sign-in, so two
    accounts can legitimately end up sharing an address — most obviously
    when a workspace address is freed and reassigned to a new person, who
    arrives with a new `sub`. This exists so callers can tell "one account"
    from "several" and say so, rather than silently picking.
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT id, google_sub, email, display_name, cash
            FROM users WHERE lower(email) = lower(%s)
            ORDER BY id
            """,
            (email,),
        )
        return cur.fetchall()


def dev_account(email):
    """Find or create the local-only account behind a dev sign-in.

    An address that already has an account signs into *that* account. The
    earlier version minted a synthetic `dev:<email>` subject unconditionally,
    which could never match a real Google `sub`, so signing in locally with
    an address that already had a Google account produced a second row with
    the same email and a separate portfolio — and left `get_by_email`
    choosing between them.
    """
    existing = get_by_email(email)
    if existing is not None:
        return existing
    return upsert_from_google(f"dev:{email}", email, email.split("@")[0])


def list_users():
    with cursor() as cur:
        cur.execute(
            "SELECT id, google_sub, email, display_name, cash FROM users ORDER BY email"
        )
        return cur.fetchall()


def get_cash(user_id):
    with cursor() as cur:
        cur.execute("SELECT cash FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None


def set_cash(user_id, amount):
    """Set a balance outright. For resets and corrections, not for trading.

    HAZARD, if you are about to call this from something that trades: this
    is a blind write. It does not lock the account row, so it does not read
    the balance it overwrites — a trade committing between your read and
    this UPDATE is silently discarded, and the cash then disagrees with the
    trade log that says it moved. It also takes the `users` lock without
    taking `portfolio` first or second, so mixing it into a trading path
    reintroduces the lock-ordering deadlock that record_purchase and
    record_sale were aligned to avoid.

    Buying and selling move cash inside the same transaction as the shares
    (see models/portfolio.py); anything that trades belongs there, not
    here. Today this is called only from tests.
    """
    amount = Decimal(amount)
    if not amount.is_finite() or amount < 0:
        raise ValueError("Cash balance must be a non-negative number.")
    with cursor(commit=True) as cur:
        cur.execute(
            "UPDATE users SET cash = %s WHERE id = %s RETURNING cash",
            (amount, user_id),
        )
        row = cur.fetchone()
        if row is None:
            raise UnknownUser(f"No account with id {user_id}.")
        return row[0]


def get_leaderboard_settings(user_id):
    """Returns (opted_in, leaderboard_name) for one account."""
    with cursor() as cur:
        cur.execute(
            "SELECT leaderboard_opt_in, leaderboard_name FROM users WHERE id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise UnknownUser(f"No account with id {user_id}.")
        return row


def set_leaderboard_settings(user_id, opt_in, leaderboard_name=None):
    """Join or leave the leaderboard, and set the name shown there.

    Opting out keeps the chosen name, so someone who leaves and rejoins is
    not made to invent a new one. The database refuses opted-in with no
    name, which is what stops the view ever having to fall back to the
    email address.
    """
    name = (leaderboard_name or "").strip() or None
    try:
        with cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE users
                SET leaderboard_opt_in = %s,
                    leaderboard_name = COALESCE(%s, leaderboard_name)
                WHERE id = %s
                RETURNING leaderboard_opt_in, leaderboard_name
                """,
                (bool(opt_in), name, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise UnknownUser(f"No account with id {user_id}.")
            return row
    except psycopg2.errors.UniqueViolation:
        # users_leaderboard_name_key. The caller checks for a clash first so
        # the usual path gives a readable message, but that check and this
        # update are two statements: two people claiming one name at the
        # same moment both pass it and the index refuses the second. Phrased
        # the same way here, so which of the two paths refused it is not
        # something the reader has to care about.
        raise ValidationError(
            "Someone is already using that name. Try another."
        ) from None


def delete_account(user_id):
    """Erase an account and everything belonging to it, in one transaction.

    The deletes are not spelled out here: `portfolio`, `trades` and
    `assistant_requests` all reference users(id) ON DELETE CASCADE, so
    removing the row removes them with it, in the same transaction, with no
    chance of a table being missed as the schema grows. Nothing of the
    person's is left behind — `stocks` and `price_history` are shared market
    data that was never theirs.

    `app.erasing_account` is what lets the cascade reach the trade log,
    which is otherwise append-only (see the trades_no_rewrite trigger).
    SET LOCAL scopes it to this transaction, so the exemption ends when the
    deletion does and cannot leak into any later query on the connection.

    Returns the deleted account's email, or None if there was no such
    account.
    """
    with cursor(commit=True) as cur:
        cur.execute("SET LOCAL app.erasing_account = 'on'")
        cur.execute("DELETE FROM users WHERE id = %s RETURNING email", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None


def resolve_cli_user():
    """Work out which account the terminal interface should act as.

    The CLI cannot run an OAuth browser redirect, so the account is named
    by PORTFOLIO_USER in .env. Raises UnknownUser with a message that says
    how to fix it rather than failing obscurely.
    """
    if config.PORTFOLIO_USER_ID:
        try:
            wanted = int(str(config.PORTFOLIO_USER_ID).strip())
        except ValueError:
            raise UnknownUser(
                f"PORTFOLIO_USER_ID must be a number, not "
                f"'{config.PORTFOLIO_USER_ID}'."
            )
        user = get_by_id(wanted)
        if user is None:
            raise UnknownUser(
                f"No account with id {wanted}. Known accounts: "
                + (_known_emails() or "none yet.")
            )
        return user

    email = config.PORTFOLIO_USER
    if not email:
        raise UnknownUser(
            "No account selected. Add PORTFOLIO_USER=you@example.com to your "
            ".env file, using the email you signed into the web app with.\n"
            "Known accounts: " + (_known_emails() or "none yet — sign in via the web app first.")
        )

    matches = list_by_email(email)
    if not matches:
        raise UnknownUser(
            f"No account found for '{email}'. Sign in to the web app with that "
            "Google account first, or correct PORTFOLIO_USER in .env.\n"
            "Known accounts: " + (_known_emails() or "none yet.")
        )
    if len(matches) > 1:
        # Silently picking one would let the terminal trade against a
        # different portfolio than the browser shows, with nothing on
        # screen to explain the discrepancy.
        listed = ", ".join(f"id {row[0]} ({row[1]})" for row in matches)
        raise UnknownUser(
            f"More than one account uses '{email}': {listed}. PORTFOLIO_USER "
            f"cannot say which one you mean. Set PORTFOLIO_USER_ID to the id "
            f"you want, or delete the account you no longer use."
        )
    return matches[0]


def _known_emails():
    return ", ".join(row[2] for row in list_users())

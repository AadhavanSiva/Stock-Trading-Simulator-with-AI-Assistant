"""Accounts and their cash balances."""
from decimal import Decimal

from portfolio_tracker import config
from portfolio_tracker.db import cursor
from portfolio_tracker.errors import UnknownUser


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
    with cursor() as cur:
        cur.execute(
            """
            SELECT id, google_sub, email, display_name, cash
            FROM users WHERE lower(email) = lower(%s)
            """,
            (email,),
        )
        return cur.fetchone()


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
    """Set a balance outright. For resets and corrections, not for trading —
    buys and sells move cash inside their own transaction so the money and
    the shares can never disagree."""
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
    email = config.PORTFOLIO_USER
    if not email:
        raise UnknownUser(
            "No account selected. Add PORTFOLIO_USER=you@example.com to your "
            ".env file, using the email you signed into the web app with.\n"
            "Known accounts: " + (_known_emails() or "none yet — sign in via the web app first.")
        )

    user = get_by_email(email)
    if user is None:
        raise UnknownUser(
            f"No account found for '{email}'. Sign in to the web app with that "
            "Google account first, or correct PORTFOLIO_USER in .env.\n"
            "Known accounts: " + (_known_emails() or "none yet.")
        )
    return user


def _known_emails():
    return ", ".join(row[2] for row in list_users())

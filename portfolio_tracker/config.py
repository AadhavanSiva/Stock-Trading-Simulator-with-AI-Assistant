import os
import secrets

from dotenv import load_dotenv

load_dotenv()

# A full connection string, as hosted databases (Neon, Render, ...) hand
# out: postgresql://user:password@host/dbname?sslmode=require. When set it
# is used as-is, query options included, and the DB_* settings below are
# ignored. Local development keeps using the DB_* settings.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or None

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "practice")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_PORT = os.getenv("DB_PORT", "5432")

# Flask session signing key. It signs the session cookie and the CSRF
# tokens, so it must stay the same across restarts and across every process
# serving the app. Required outside debug mode; see flask_secret_key().
# Generate one with:
#   python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "").strip() or None

# Set when the app runs behind a proxy that terminates HTTPS (Render, Heroku,
# a load balancer). The app then trusts one hop of X-Forwarded-* headers, so
# it builds https:// links (Google sign-in rejects an http:// callback), and
# marks the session cookie Secure. Leave unset locally: without a proxy in
# front, anyone could send those headers.
BEHIND_HTTPS_PROXY = os.getenv("BEHIND_HTTPS_PROXY", "").strip().lower() in ("1", "true", "yes")


class ConfigurationError(RuntimeError):
    """A setting the app cannot safely run without is missing."""


def flask_secret_key(debug):
    """The key Flask signs sessions with.

    In production a missing key must stop the app at startup. Silently
    generating a random one would sign everyone out on every restart, and
    with more than one worker process each would reject the others'
    sessions and CSRF tokens, which looks like random failures. Local
    development (debug mode) still gets a throwaway key so the app runs
    without setup.
    """
    if SECRET_KEY:
        return SECRET_KEY
    if debug:
        return secrets.token_hex(32)
    raise ConfigurationError(
        "FLASK_SECRET_KEY is not set. It is required when the app is not "
        "running in debug mode. Generate one with\n"
        '    python -c "import secrets; print(secrets.token_hex(32))"\n'
        "and add FLASK_SECRET_KEY=<that value> to .env (or the server's "
        "environment), then start the app again."
    )


# --- Market data ------------------------------------------------------------
# Upper bounds, in seconds, on a single Yahoo Finance request. A quote backs a
# page load, so it gives up sooner; a full price history can be large.
MARKET_QUOTE_TIMEOUT = float(os.getenv("MARKET_QUOTE_TIMEOUT", "8"))
MARKET_HISTORY_TIMEOUT = float(os.getenv("MARKET_HISTORY_TIMEOUT", "20"))
# How long a looked-up quote is reused for page views before asking Yahoo
# again. Buying, selling and "Refresh prices" always fetch a fresh price.
QUOTE_CACHE_SECONDS = float(os.getenv("QUOTE_CACHE_SECONDS", "30"))

# --- Google sign-in -------------------------------------------------------
# Create these at console.cloud.google.com: APIs & Services > Credentials >
# OAuth client ID > Web application, with the redirect URI
#   http://127.0.0.1:5000/auth/callback
# Then put the two values in .env. The app tells you this on /login if they
# are missing, rather than failing with a stack trace.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

def google_configured():
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

# Opt-in local sign-in, for trying the app before wiring up Google. It is
# OFF unless you set ALLOW_DEV_LOGIN=1, and the UI says plainly when it is on.
ALLOW_DEV_LOGIN = os.getenv("ALLOW_DEV_LOGIN", "").strip() in ("1", "true", "yes")

# Which account the terminal interface acts as. The CLI cannot run a browser
# redirect, so it reads the account from here instead.
PORTFOLIO_USER = os.getenv("PORTFOLIO_USER")

# Cash every new account starts with, in dollars.
STARTING_CASH = os.getenv("STARTING_CASH", "50000")

# --- AI assistant -----------------------------------------------------------
# Runs on Google's Gemini API. The SDK reads the key from GEMINI_API_KEY (or
# GOOGLE_API_KEY) itself; nothing here reads or stores it. GEMINI_API_KEY is
# the one to use, so it is not confused with the GOOGLE_CLIENT_* sign-in
# settings above. Without a key the assistant explains how to add one.
ASSISTANT_ENABLED = os.getenv("ASSISTANT_ENABLED", "1").strip().lower() not in ("0", "false", "no")
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL", "gemini-3.8-flash")
# How much the model reasons before answering: low, medium or high.
ASSISTANT_THINKING = os.getenv("ASSISTANT_THINKING", "medium").strip().lower()
# Let the assistant search Google for news, earnings and background that the
# app does not store. On Gemini 3 models each search it runs is billed.
ASSISTANT_SEARCH = os.getenv("ASSISTANT_SEARCH", "1").strip().lower() not in ("0", "false", "no")

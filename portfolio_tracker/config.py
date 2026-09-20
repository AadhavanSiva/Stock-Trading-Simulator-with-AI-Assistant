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
# Alpaca. Create a (free, paper) account at alpaca.markets, then
# Home > API Keys > Generate. Both values are shown once; the secret
# cannot be retrieved later, only regenerated.
ALPACA_API_KEY_ID = os.getenv("ALPACA_API_KEY_ID", "").strip() or None
ALPACA_API_SECRET_KEY = os.getenv("ALPACA_API_SECRET_KEY", "").strip() or None

# Prices and the asset catalogue live on different hosts. The catalogue is
# part of the trading API, and the paper host serves it with paper keys.
ALPACA_DATA_URL = os.getenv(
    "ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/")
ALPACA_TRADING_URL = os.getenv(
    "ALPACA_TRADING_URL", "https://paper-api.alpaca.markets").rstrip("/")

# Which feed to price from. The free plan serves IEX; "sip" is the
# consolidated tape across every US exchange and needs a paid plan, which
# Alpaca refuses with a 403 rather than quietly downgrading.
ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex").strip().lower()


def alpaca_credentials():
    """The API key pair, or a message saying how to get one.

    Raised rather than returned empty so a missing key fails at the call
    that needs it, with instructions, instead of arriving as a puzzling
    401 from the other side.
    """
    if ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY:
        return ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY
    raise ConfigurationError(
        "Alpaca API keys are not set. Create a free account at "
        "alpaca.markets, then Home > API Keys > Generate, and add\n"
        "    ALPACA_API_KEY_ID=<your key id>\n"
        "    ALPACA_API_SECRET_KEY=<your secret key>\n"
        "to .env (or the server's environment)."
    )


def alpaca_configured():
    return bool(ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY)


# Upper bounds, in seconds, on a single market data request. A quote backs
# a page load, so it gives up sooner; a full price history can be large.
MARKET_QUOTE_TIMEOUT = float(os.getenv("MARKET_QUOTE_TIMEOUT", "8"))
MARKET_HISTORY_TIMEOUT = float(os.getenv("MARKET_HISTORY_TIMEOUT", "20"))
# How long a looked-up quote is reused for page views before asking Alpaca
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
# An unambiguous alternative, by account id. Email is not unique — two
# accounts can share an address — so this is the way to name one exactly
# when PORTFOLIO_USER would be ambiguous. Takes precedence when both are set.
PORTFOLIO_USER_ID = os.getenv("PORTFOLIO_USER_ID")

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

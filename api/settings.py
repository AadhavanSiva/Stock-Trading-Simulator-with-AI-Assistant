"""Settings that only the API needs.

Everything shared with the Flask app and the CLI (database, Google client,
market data, starting cash) stays in portfolio_tracker.config; this module
reads it from there rather than duplicating it.
"""
import logging
import os
import secrets

from portfolio_tracker import config
from portfolio_tracker.config import ConfigurationError

log = logging.getLogger(__name__)

# Signs the API's access tokens. Separate from FLASK_SECRET_KEY so the two
# front ends can be rotated independently, and so a token minted for one
# can never be mistaken for a cookie signed by the other.
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "").strip() or None

# How long a sign-in lasts. There is no refresh token: when this runs out
# the web UI sends the reader back to the sign-in page.
JWT_TTL_MINUTES = int(os.getenv("JWT_TTL_MINUTES", "720"))

# Origins allowed to call the API from a browser. The Angular dev server
# proxies /api, so local development needs none of these; they matter when
# the UI is served from a different origin than the API.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "API_CORS_ORIGINS", "http://localhost:8200,http://127.0.0.1:8200"
    ).split(",")
    if origin.strip()
]

IS_PRODUCTION = config.ENVIRONMENT == "production"

_throwaway_key = None


def jwt_secret_key():
    """The key tokens are signed with.

    Required in production, for the same reason FLASK_SECRET_KEY is: a key
    invented at startup signs everyone out on every restart and differs
    between worker processes. Local development gets a throwaway key so
    the API starts without setup, with a warning that sign-ins will not
    survive a restart.
    """
    global _throwaway_key
    if JWT_SECRET_KEY:
        return JWT_SECRET_KEY
    if IS_PRODUCTION:
        raise ConfigurationError(
            "JWT_SECRET_KEY is not set. It is required in production. "
            "Generate one with\n"
            '    python -c "import secrets; print(secrets.token_hex(32))"\n'
            "and add JWT_SECRET_KEY=<that value> to the server's environment."
        )
    if _throwaway_key is None:
        log.warning("JWT_SECRET_KEY is not set; using a throwaway key. "
                    "Every sign-in will end when the API restarts.")
        _throwaway_key = secrets.token_hex(32)
    return _throwaway_key

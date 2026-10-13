"""Access tokens, Google sign-in, and the signed-in account dependency.

The browser signs in with Google Identity Services, which hands it a
Google ID token. The API verifies that token with Google's public keys,
finds or creates the account, and answers with its own short-lived JWT.
Every other request carries that JWT as a bearer token.

The JWT names the account and nothing else. Cash, email and the rest are
read from the database on each request, so a token never outlives the
account it names: delete the account and its tokens stop working.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from api import settings
from portfolio_tracker import config
from portfolio_tracker.models import users

ALGORITHM = "HS256"
ISSUER = "portfolio-tracker-api"

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    display_name: str | None
    cash: Decimal


def issue_token(user_id):
    """A signed access token for this account, and its lifetime in seconds."""
    now = datetime.now(timezone.utc)
    lifetime = timedelta(minutes=settings.JWT_TTL_MINUTES)
    token = jwt.encode(
        {"sub": str(user_id), "iss": ISSUER, "iat": now, "exp": now + lifetime},
        settings.jwt_secret_key(),
        algorithm=ALGORITHM,
    )
    return token, int(lifetime.total_seconds())


def _unauthorized(message):
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    """The account a request's bearer token names. 401 for anything else."""
    if credentials is None:
        raise _unauthorized("Sign in to continue.")
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key(),
            # Pinned, never read from the token: accepting whatever "alg"
            # the token claims is how "none" and key-confusion attacks work.
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        user_id = int(claims["sub"])
    except (jwt.PyJWTError, ValueError):
        raise _unauthorized("Your sign-in has expired. Please sign in again.")

    account = users.get_by_id(user_id)
    if account is None:
        raise _unauthorized("That account no longer exists. Please sign in again.")
    account_id, _, email, display_name, cash = account
    return CurrentUser(id=account_id, email=email, display_name=display_name, cash=cash)


def verify_google_credential(credential):
    """Check a Google ID token and return its claims.

    Verifies the signature against Google's published keys, the issuer,
    the expiry, and that the token was minted for *this* app's client id —
    a token Google issued to some other site must not sign in here.
    Raises ValueError for anything that fails.
    """
    return id_token.verify_oauth2_token(
        credential, google_requests.Request(), config.GOOGLE_CLIENT_ID,
    )

"""Sign-in: Google, the local dev form, and who is signed in."""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api import schemas, security
from api.security import CurrentUser, current_user
from portfolio_tracker import config
from portfolio_tracker.models import users

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _signed_in(account, new_account):
    account_id, _, email, display_name, cash = account
    token, expires_in = security.issue_token(account_id)
    return schemas.TokenOut(
        access_token=token,
        expires_in=expires_in,
        new_account=new_account,
        user=schemas.user_out(CurrentUser(account_id, email, display_name, cash)),
    )


@router.get("/config", response_model=schemas.AuthConfig)
def auth_config():
    """Which sign-in methods are available. Public.

    The Google client id is not a secret — Google's own button embeds it
    in every page that uses it — and the UI needs it to draw the button.
    """
    return schemas.AuthConfig(
        google_client_id=config.GOOGLE_CLIENT_ID if config.google_configured() else None,
        dev_login=config.ALLOW_DEV_LOGIN,
        starting_cash=users.starting_cash(),
    )


@router.post("/google", response_model=schemas.TokenOut)
def sign_in_with_google(body: schemas.GoogleSignIn):
    """Exchange a Google ID token for an access token.

    The first sign-in creates the account; Google draws no distinction
    between signing up and logging in.
    """
    if not config.google_configured():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Google sign-in is not configured.")

    try:
        claims = security.verify_google_credential(body.credential)
    except Exception:
        # Usually an expired or replayed token. The details are for the
        # log, not the response.
        log.warning("Google credential rejected", exc_info=True)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Google sign-in failed. Please try signing in again.")

    # `sub` is Google's stable account id. Email can change; sub cannot.
    if not claims.get("sub"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Google did not return an account id. Sign-in cancelled.")

    returning = users.get_by_google_sub(claims["sub"]) is not None
    account = users.upsert_from_google(
        claims["sub"], claims.get("email", ""), claims.get("name"))
    return _signed_in(account, new_account=not returning)


@router.post("/dev", response_model=schemas.TokenOut)
def sign_in_locally(body: schemas.DevSignIn):
    """Local-only sign-in for trying the app before Google is configured.

    404 unless ALLOW_DEV_LOGIN is set, and config refuses to start with it
    set in production.
    """
    if not config.ALLOW_DEV_LOGIN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Enter an email address to sign in with.")
    existed = users.get_by_email(email) is not None
    return _signed_in(users.dev_account(email), new_account=not existed)


@router.get("/me", response_model=schemas.UserOut)
def me(user: CurrentUser = Depends(current_user)):
    """The signed-in account, read fresh from the database."""
    return schemas.user_out(user)

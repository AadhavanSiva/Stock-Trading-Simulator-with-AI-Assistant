"""Google sign-in tests.

No network and no real Google credentials. The OAuth client is either
mocked, or registered with explicit endpoints so Authlib builds a real
authorization URL we can inspect.
"""
from unittest.mock import MagicMock, patch

import pytest

import web as web_module
from portfolio_tracker import config
from portfolio_tracker.models import users


def configured():
    """Pretend GOOGLE_CLIENT_ID / SECRET are present in .env."""
    return patch.object(config, "google_configured", return_value=True)


def google_returns(claims):
    """Stand in for the Google client, returning these ID-token claims."""
    fake = MagicMock()
    fake.google.authorize_access_token.return_value = {"userinfo": claims}
    return patch.object(web_module, "oauth", fake)


def text(response):
    return response.get_data(as_text=True)


class TestSafeNext:
    @pytest.mark.parametrize("target", ["/buy", "/sell/AAPL", "/balance"])
    def test_internal_paths_are_allowed(self, target):
        assert web_module.safe_next(target) == target

    @pytest.mark.parametrize("target", [
        "https://evil.example/x",      # absolute URL
        "//evil.example/x",            # protocol-relative
        "http://evil.example",
        "/\\evil.example",             # backslash trick
        "",
        None,
    ])
    def test_offsite_targets_are_refused(self, target):
        """An unchecked `next` is an open redirect."""
        assert web_module.safe_next(target) is None


class TestAuthorizationRedirect:
    def test_unconfigured_google_does_not_pretend_to_work(self, anon):
        response = anon.get("/login/google", follow_redirects=True)
        assert "not configured" in text(response)

    def test_builds_a_real_google_authorization_url(self, anon):
        """Registers a genuine Authlib client with explicit endpoints (no
        discovery call) and checks the URL it sends the browser to."""
        from authlib.integrations.flask_client import OAuth

        oauth = OAuth(web_module.app)
        oauth.register(
            name="google",
            client_id="test-client-id.apps.googleusercontent.com",
            client_secret="test-secret",
            access_token_url="https://oauth2.googleapis.com/token",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            client_kwargs={"scope": "openid email profile"},
        )

        with configured(), patch.object(web_module, "oauth", oauth):
            response = anon.get("/login/google")

        assert response.status_code == 302
        location = response.headers["Location"]
        assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
        assert "client_id=test-client-id.apps.googleusercontent.com" in location
        assert "scope=openid+email+profile" in location
        assert "response_type=code" in location
        assert "auth%2Fcallback" in location


class TestCallback:
    CLAIMS = {"sub": "google-sub-123", "email": "ann@example.com", "name": "Ann"}

    def test_successful_sign_in_creates_the_account(self, anon):
        with configured(), google_returns(self.CLAIMS):
            response = anon.get("/auth/callback", follow_redirects=True)

        # A first-ever sign-in is a sign-up, and says so.
        assert "Your account is ready" in text(response)
        account = users.get_by_email("ann@example.com")
        assert account is not None
        assert account[1] == "google-sub-123"

    def test_session_is_established(self, anon):
        with configured(), google_returns(self.CLAIMS):
            anon.get("/auth/callback")
        # The portfolio page is behind the auth gate; reaching it proves the
        # session took.
        assert anon.get("/").status_code == 200

    def test_new_account_gets_the_starting_cash(self, anon):
        with configured(), google_returns(self.CLAIMS):
            anon.get("/auth/callback")
        account = users.get_by_email("ann@example.com")
        assert account[4] == users.starting_cash()

    def test_returning_user_reuses_the_same_account(self, anon):
        with configured(), google_returns(self.CLAIMS):
            anon.get("/auth/callback")
            anon.get("/logout")
            anon.get("/auth/callback")
        assert len(users.list_users()) == 1

    def test_a_returning_user_is_greeted_as_such_not_welcomed_again(self, anon):
        with configured(), google_returns(self.CLAIMS):
            anon.get("/auth/callback")
            anon.get("/logout")
            response = anon.get("/auth/callback", follow_redirects=True)
        body = text(response)
        assert "Signed in as ann@example.com" in body
        assert "Your account is ready" not in body

    def test_a_changed_email_does_not_create_a_second_account(self, anon):
        """Identity is Google's `sub`. Emails change; accounts should not."""
        with configured(), google_returns(self.CLAIMS):
            anon.get("/auth/callback")
        moved = dict(self.CLAIMS, email="ann.new@example.com")
        with configured(), google_returns(moved):
            anon.get("/auth/callback")

        assert len(users.list_users()) == 1
        assert users.get_by_email("ann.new@example.com") is not None

    def test_two_different_google_accounts_stay_separate(self, anon):
        with configured(), google_returns(self.CLAIMS):
            anon.get("/auth/callback")
            anon.get("/logout")
        other = {"sub": "google-sub-999", "email": "bob@example.com", "name": "Bob"}
        with configured(), google_returns(other):
            anon.get("/auth/callback")

        assert len(users.list_users()) == 2

    def test_missing_sub_is_refused(self, anon):
        """Without a stable subject claim there is no identity to key on."""
        with configured(), google_returns({"email": "ann@example.com"}):
            response = anon.get("/auth/callback", follow_redirects=True)
        assert "did not return an account id" in text(response)
        assert users.list_users() == []

    def test_a_failed_token_exchange_is_reported_not_crashed(self, anon):
        fake = MagicMock()
        fake.google.authorize_access_token.side_effect = RuntimeError("bad state")
        with configured(), patch.object(web_module, "oauth", fake):
            response = anon.get("/auth/callback", follow_redirects=True)
        assert "Google sign-in failed" in text(response)
        assert users.list_users() == []

    def test_callback_without_configuration_goes_back_to_login(self, anon):
        response = anon.get("/auth/callback")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


class TestReturnToWhereYouWereHeaded:
    CLAIMS = {"sub": "sub-x", "email": "x@example.com", "name": "X"}

    def test_signing_in_returns_you_to_the_page_you_asked_for(self, anon):
        # Hitting a protected page while signed out records the destination.
        anon.get("/balance")
        anon.get("/login?next=%2Fbalance")

        with configured(), google_returns(self.CLAIMS):
            response = anon.get("/auth/callback")

        assert response.headers["Location"].endswith("/balance")

    def test_an_offsite_next_is_ignored(self, anon):
        anon.get("/login?next=https%3A%2F%2Fevil.example%2Fx")

        with configured(), google_returns(self.CLAIMS):
            response = anon.get("/auth/callback")

        assert "evil.example" not in response.headers["Location"]

    def test_protected_page_records_where_you_were_going(self, anon):
        response = anon.get("/balance")
        assert "next=/balance" in response.headers["Location"]


class TestDevLogin:
    def test_disabled_by_default(self, anon):
        assert anon.post("/login/dev", data={"email": "a@b.com"}).status_code == 404

    def test_works_when_explicitly_enabled(self, anon):
        with patch.object(config, "ALLOW_DEV_LOGIN", True):
            response = anon.post("/login/dev", data={"email": "a@b.com"},
                                 follow_redirects=True)
        assert "Signed in locally" in text(response)

    def test_requires_an_email(self, anon):
        with patch.object(config, "ALLOW_DEV_LOGIN", True):
            response = anon.post("/login/dev", data={"email": "  "},
                                 follow_redirects=True)
        assert "Enter an email address" in text(response)
        assert users.list_users() == []

    def test_dev_accounts_are_namespaced_away_from_real_google_ones(self, anon):
        """A dev sign-in must never collide with a real Google `sub`."""
        with patch.object(config, "ALLOW_DEV_LOGIN", True):
            anon.post("/login/dev", data={"email": "ann@example.com"})
        account = users.get_by_email("ann@example.com")
        assert account[1].startswith("dev:")

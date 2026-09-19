"""Account tests: identity, cash, and how the CLI picks an account."""
from decimal import Decimal
from unittest.mock import patch

import pytest

from portfolio_tracker import config
from portfolio_tracker.errors import UnknownUser
from portfolio_tracker.models import users

STARTING = Decimal("50000")


class TestGoogleIdentity:
    def test_first_sign_in_creates_the_account(self, db):
        account = users.upsert_from_google("sub-1", "a@example.com", "Ann")
        user_id, sub, email, name, cash = account
        assert sub == "sub-1"
        assert email == "a@example.com"
        assert cash == STARTING

    def test_signing_in_again_reuses_the_same_account(self, db):
        first = users.upsert_from_google("sub-1", "a@example.com", "Ann")
        second = users.upsert_from_google("sub-1", "a@example.com", "Ann")
        assert first[0] == second[0]
        assert len(users.list_users()) == 1

    def test_a_changed_email_keeps_the_same_account(self, db):
        """Identity is Google's `sub`, not the email — people change emails."""
        first = users.upsert_from_google("sub-1", "old@example.com", "Ann")
        second = users.upsert_from_google("sub-1", "new@example.com", "Ann")
        assert first[0] == second[0]
        assert second[2] == "new@example.com"

    def test_different_subs_are_different_accounts(self, db):
        a = users.upsert_from_google("sub-1", "a@example.com", "Ann")
        b = users.upsert_from_google("sub-2", "b@example.com", "Bob")
        assert a[0] != b[0]

    def test_signing_in_again_does_not_reset_cash(self, db):
        account = users.upsert_from_google("sub-1", "a@example.com", "Ann")
        users.set_cash(account[0], Decimal("123.45"))
        users.upsert_from_google("sub-1", "a@example.com", "Ann")
        assert users.get_cash(account[0]) == Decimal("123.45")

    def test_lookup_by_email_is_case_insensitive(self, db):
        users.upsert_from_google("sub-1", "Ann@Example.com", "Ann")
        assert users.get_by_email("ann@example.com") is not None


class TestCash:
    def test_new_accounts_start_with_the_configured_amount(self, user):
        assert users.get_cash(user) == STARTING

    def test_set_cash(self, user):
        assert users.set_cash(user, Decimal("1234.50")) == Decimal("1234.50")

    @pytest.mark.parametrize("bad", ["-1", "NaN"])
    def test_set_cash_refuses_nonsense(self, user, bad):
        with pytest.raises(ValueError):
            users.set_cash(user, Decimal(bad))

    def test_set_cash_on_a_missing_account(self, db):
        with pytest.raises(UnknownUser):
            users.set_cash(99999, Decimal("100"))


class TestCliUserResolution:
    def test_resolves_the_account_named_in_env(self, user):
        with patch.object(config, "PORTFOLIO_USER", "tester@example.com"):
            assert users.resolve_cli_user()[0] == user

    def test_missing_setting_explains_what_to_add(self, db):
        with patch.object(config, "PORTFOLIO_USER", None):
            with pytest.raises(UnknownUser) as exc:
                users.resolve_cli_user()
        assert "PORTFOLIO_USER" in str(exc.value)

    def test_unknown_email_lists_the_known_accounts(self, user):
        with patch.object(config, "PORTFOLIO_USER", "nobody@example.com"):
            with pytest.raises(UnknownUser) as exc:
                users.resolve_cli_user()
        message = str(exc.value)
        assert "No account found" in message
        assert "tester@example.com" in message


class TestTwoAccountsSharingAnEmail:
    """`email` is not unique, so every read of it must be deliberate.

    This is the shape that bit us: dev login minted a synthetic
    `dev:<email>` subject that could never match a real Google `sub`, so
    signing in locally with an address that already had an account created
    a second row beside it — and `get_by_email` then chose between them
    with no ORDER BY, so the answer could change between calls.
    """

    @pytest.fixture
    def twins(self, db):
        first = users.upsert_from_google("google-sub-real", "ann@example.com", "Ann")
        second = users.upsert_from_google("dev:ann@example.com", "ann@example.com", "ann")
        return first[0], second[0]

    def test_the_schema_permits_it(self, twins):
        assert len(users.list_by_email("ann@example.com")) == 2

    def test_the_lookup_is_deterministic(self, twins):
        """The same question must not get different answers."""
        answers = {users.get_by_email("ann@example.com")[0] for _ in range(8)}
        assert len(answers) == 1

    def test_the_lookup_prefers_the_older_account(self, twins):
        original, _ = twins
        assert users.get_by_email("ann@example.com")[0] == original

    def test_listing_returns_both_oldest_first(self, twins):
        assert [row[0] for row in users.list_by_email("ann@example.com")] == list(twins)

    def test_the_cli_refuses_to_guess(self, twins, monkeypatch):
        """Silently picking would trade against a different portfolio than
        the browser shows, with nothing on screen to explain it."""
        monkeypatch.setattr(config, "PORTFOLIO_USER", "ann@example.com")
        monkeypatch.setattr(config, "PORTFOLIO_USER_ID", None)
        with pytest.raises(UnknownUser, match="More than one account"):
            users.resolve_cli_user()

    def test_the_refusal_names_the_accounts(self, twins, monkeypatch):
        monkeypatch.setattr(config, "PORTFOLIO_USER", "ann@example.com")
        monkeypatch.setattr(config, "PORTFOLIO_USER_ID", None)
        with pytest.raises(UnknownUser) as caught:
            users.resolve_cli_user()
        for user_id in twins:
            assert f"id {user_id}" in str(caught.value)

    def test_an_id_resolves_it(self, twins, monkeypatch):
        _, second = twins
        monkeypatch.setattr(config, "PORTFOLIO_USER", "ann@example.com")
        monkeypatch.setattr(config, "PORTFOLIO_USER_ID", str(second))
        assert users.resolve_cli_user()[0] == second

    def test_a_single_account_still_resolves_by_email(self, db, monkeypatch):
        account = users.upsert_from_google("sub-solo", "solo@example.com", "Solo")
        monkeypatch.setattr(config, "PORTFOLIO_USER", "solo@example.com")
        monkeypatch.setattr(config, "PORTFOLIO_USER_ID", None)
        assert users.resolve_cli_user()[0] == account[0]


class TestPortfolioUserId:
    def test_it_wins_over_the_email(self, db, monkeypatch):
        wanted = users.upsert_from_google("sub-a", "a@example.com", "A")[0]
        users.upsert_from_google("sub-b", "b@example.com", "B")
        monkeypatch.setattr(config, "PORTFOLIO_USER", "b@example.com")
        monkeypatch.setattr(config, "PORTFOLIO_USER_ID", str(wanted))
        assert users.resolve_cli_user()[0] == wanted

    def test_an_unknown_id_says_so(self, db, monkeypatch):
        monkeypatch.setattr(config, "PORTFOLIO_USER_ID", "999999")
        with pytest.raises(UnknownUser, match="No account with id 999999"):
            users.resolve_cli_user()

    def test_a_non_numeric_id_says_so(self, db, monkeypatch):
        monkeypatch.setattr(config, "PORTFOLIO_USER_ID", "ann@example.com")
        with pytest.raises(UnknownUser, match="must be a number"):
            users.resolve_cli_user()


class TestDevSignIn:
    def test_it_reuses_an_existing_account(self, db):
        """The bug: this used to create a second row with the same email."""
        real = users.upsert_from_google("google-sub-real", "ann@example.com", "Ann")
        assert users.dev_account("ann@example.com")[0] == real[0]
        assert len(users.list_by_email("ann@example.com")) == 1

    def test_it_creates_one_when_there_is_none(self, db):
        account = users.dev_account("new@example.com")
        assert account[2] == "new@example.com"
        assert account[1] == "dev:new@example.com"

    def test_it_is_idempotent(self, db):
        first = users.dev_account("new@example.com")
        assert users.dev_account("new@example.com")[0] == first[0]
        assert len(users.list_by_email("new@example.com")) == 1

    def test_the_route_does_not_fork_the_account(self, db, monkeypatch):
        """Signing in locally must land on the portfolio you already have."""
        import web as web_module

        real = users.upsert_from_google("google-sub-real", "ann@example.com", "Ann")
        monkeypatch.setattr(config, "ALLOW_DEV_LOGIN", True)
        web_module.app.config.update(TESTING=True)
        monkeypatch.setitem(web_module.app.config, "WTF_CSRF_ENABLED", False)

        with web_module.app.test_client() as c:
            c.post("/login/dev", data={"email": "ann@example.com"})
            with c.session_transaction() as session:
                assert session["user_id"] == real[0]
        assert len(users.list_by_email("ann@example.com")) == 1

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

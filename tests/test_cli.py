"""CLI tests. Input and market data are mocked so nothing blocks or hits the network."""
from decimal import Decimal
from unittest.mock import patch

import pytest

from portfolio_tracker import cli
from portfolio_tracker.models import portfolio, users
from portfolio_tracker.services import market_data

STARTING = Decimal("50000")


def answers(*responses):
    """Feed a scripted sequence of replies to input()."""
    return patch("builtins.input", side_effect=list(responses))


def quote(price, name="Test Co"):
    value = Decimal(str(price)) if price is not None else None
    return patch.object(market_data, "get_quote", return_value=(value, name))


def live_price(price):
    value = Decimal(str(price)) if price is not None else None
    return patch.object(market_data, "get_live_price", return_value=value)


class TestFormatShares:
    def test_trims_trailing_zeros(self):
        assert cli.format_shares(Decimal("10.000")) == "10"

    def test_keeps_real_precision(self):
        assert cli.format_shares(Decimal("2.50")) == "2.5"

    def test_no_scientific_notation(self):
        assert cli.format_shares(Decimal("1000")) == "1000"


class TestAskDecimal:
    def test_accepts_a_number(self):
        with answers("5"):
            assert cli.ask_decimal("? ") == Decimal("5")

    def test_retries_after_junk(self, capsys):
        with answers("abc", "3"):
            assert cli.ask_decimal("? ") == Decimal("3")
        assert "not a number" in capsys.readouterr().out

    @pytest.mark.parametrize("word", ["c", "cancel", "q", "quit"])
    def test_cancel_words_return_none(self, word):
        with answers(word):
            assert cli.ask_decimal("? ") is None

    @pytest.mark.parametrize("bad", ["nan", "inf", "-Infinity"])
    def test_rejects_nan_and_infinity(self, bad, capsys):
        """These parse as valid Decimals and slip past a plain '<= 0' guard."""
        with answers(bad, "2"):
            assert cli.ask_decimal("? ") == Decimal("2")
        assert "not a real quantity" in capsys.readouterr().out

    @pytest.mark.parametrize("bad", ["0", "-5"])
    def test_rejects_non_positive(self, bad, capsys):
        with answers(bad, "2"):
            assert cli.ask_decimal("? ") == Decimal("2")
        assert "greater than zero" in capsys.readouterr().out

    def test_enforces_a_maximum(self, capsys):
        with answers("100", "5"):
            assert cli.ask_decimal("? ", maximum=Decimal("10")) == Decimal("5")
        assert "You only have" in capsys.readouterr().out

    def test_all_returns_the_maximum(self):
        with answers("a"):
            got = cli.ask_decimal("? ", maximum=Decimal("7"), allow_all=True)
        assert got == Decimal("7")

    def test_ctrl_d_cancels_instead_of_crashing(self):
        with patch("builtins.input", side_effect=EOFError):
            with pytest.raises(cli.Cancelled):
                cli.ask_decimal("? ")


class TestAddStock:
    def test_adds_a_new_holding(self, user, capsys):
        with quote("150.25", "Test Co"), answers("tst", "4", "y"):
            cli.add_stock(user)

        assert portfolio.get_holding(user, "TST") == (Decimal("4"), Decimal("150.25"))
        assert "Added 4 shares" in capsys.readouterr().out

    def test_debits_cash_and_reports_it(self, user, capsys):
        with quote("100"), answers("TST", "10", "y"):
            cli.add_stock(user)
        assert users.get_cash(user) == STARTING - Decimal("1000")
        assert "Cash remaining: $49,000.00" in capsys.readouterr().out

    def test_shows_buying_power(self, user, capsys):
        with quote("100"), answers("TST", "1", "y"):
            cli.add_stock(user)
        assert "enough for 500 shares" in capsys.readouterr().out

    def test_ticker_is_upper_cased(self, user):
        with quote("10"), answers("  tst  ", "1", "y"):
            cli.add_stock(user)
        assert portfolio.get_holding(user, "TST") is not None

    def test_price_is_stored_as_exact_decimal(self, user):
        with quote("150.25"), answers("TST", "3", "y"):
            cli.add_stock(user)
        shares, cost = portfolio.get_holding(user, "TST")
        assert cost == Decimal("150.25")

    def test_declining_confirmation_adds_nothing(self, user, capsys):
        with quote("10"), answers("TST", "5", "n"):
            cli.add_stock(user)
        assert portfolio.get_holding(user, "TST") is None
        assert users.get_cash(user) == STARTING
        assert "Nothing was added" in capsys.readouterr().out

    def test_cannot_buy_more_than_the_cash_allows(self, user, capsys):
        """Asking for 1000 shares at $100 exceeds $50,000; the prompt re-asks."""
        with quote("100"), answers("TST", "1000", "5", "y"):
            cli.add_stock(user)
        out = capsys.readouterr().out
        assert "You only have" in out
        assert portfolio.get_holding(user, "TST")[0] == Decimal("5")

    def test_unknown_ticker_is_reported(self, user, capsys):
        with quote(None), answers("BOGUS"):
            cli.add_stock(user)
        assert "No market data found" in capsys.readouterr().out

    def test_empty_ticker_is_rejected(self, user, capsys):
        with answers(""):
            cli.add_stock(user)
        assert "No ticker entered" in capsys.readouterr().out

    def test_lookup_failure_is_reported(self, user, capsys):
        with patch.object(market_data, "get_quote", side_effect=RuntimeError("boom")):
            with answers("TST"):
                cli.add_stock(user)
        assert "Could not look up" in capsys.readouterr().out

    def test_second_buy_merges_and_reports_the_average(self, user, capsys):
        with quote("100"), answers("TST", "10", "y"):
            cli.add_stock(user)
        with quote("200"), answers("TST", "5", "y"):
            cli.add_stock(user)

        shares, cost = portfolio.get_holding(user, "TST")
        assert shares == Decimal("15")
        assert cost == pytest.approx(Decimal("133.3333"), abs=Decimal("0.0001"))
        assert "average cost" in capsys.readouterr().out


class TestSellStock:
    @pytest.fixture
    def holder(self, user):
        with quote("100"), answers("TST", "10", "y"):
            cli.add_stock(user)
        return user

    def test_partial_sale(self, holder, capsys):
        with quote("150"), live_price("150"), answers("TST", "4", "y"):
            cli.sell_stock(holder)
        assert portfolio.get_holding(holder, "TST")[0] == Decimal("6")
        assert "Sold 4 shares" in capsys.readouterr().out

    def test_sale_credits_cash(self, holder, capsys):
        with quote("150"), live_price("150"), answers("TST", "4", "y"):
            cli.sell_stock(holder)
        assert users.get_cash(holder) == STARTING - Decimal("1000") + Decimal("600")
        assert "Cash available" in capsys.readouterr().out

    def test_selling_all_closes_the_position(self, holder, capsys):
        with quote("150"), live_price("150"), answers("TST", "a", "y"):
            cli.sell_stock(holder)
        assert portfolio.get_holding(holder, "TST") is None
        assert "closed" in capsys.readouterr().out

    def test_overselling_is_refused_then_retried(self, holder, capsys):
        with quote("150"), live_price("150"), answers("TST", "999", "2", "y"):
            cli.sell_stock(holder)
        assert portfolio.get_holding(holder, "TST")[0] == Decimal("8")
        assert "You only have" in capsys.readouterr().out

    def test_declining_confirmation_sells_nothing(self, holder, capsys):
        with quote("150"), live_price("150"), answers("TST", "5", "n"):
            cli.sell_stock(holder)
        assert portfolio.get_holding(holder, "TST")[0] == Decimal("10")
        assert "Sale cancelled" in capsys.readouterr().out

    def test_selling_what_you_do_not_hold(self, user, capsys):
        with answers("NOPE"):
            cli.sell_stock(user)
        assert "don't hold any" in capsys.readouterr().out

    def test_missing_price_cancels_the_sale(self, holder, capsys):
        with live_price(None), answers("TST"):
            cli.sell_stock(holder)
        assert "Could not get a current price" in capsys.readouterr().out
        assert portfolio.get_holding(holder, "TST")[0] == Decimal("10")


class TestBalance:
    def test_shows_cash_and_total(self, user, capsys):
        cli.show_balance(user)
        out = capsys.readouterr().out
        assert "$50,000.00" in out
        assert "Total account value" in out

    def test_reflects_a_purchase(self, user, capsys):
        with quote("100"), answers("TST", "10", "y"):
            cli.add_stock(user)
        capsys.readouterr()
        cli.show_balance(user)
        out = capsys.readouterr().out
        assert "$49,000.00" in out   # cash
        assert "$1,000.00" in out    # investments


class TestMenu:
    @pytest.fixture
    def signed_in(self, user):
        account = users.get_by_id(user)
        return patch.object(users, "resolve_cli_user", return_value=account)

    def test_exit_key_is_derived_from_the_actions(self):
        assert cli.EXIT_KEY == str(len(cli.MENU_ACTIONS) + 1)

    def test_exit_option_quits(self, signed_in, capsys):
        with signed_in, answers(cli.EXIT_KEY):
            cli.run()
        assert "Goodbye" in capsys.readouterr().out

    def test_menu_shows_who_is_signed_in(self, signed_in, capsys):
        with signed_in, answers(cli.EXIT_KEY):
            cli.run()
        assert "Tester" in capsys.readouterr().out

    def test_ctrl_d_at_the_menu_exits_cleanly(self, signed_in, capsys):
        with signed_in, patch("builtins.input", side_effect=EOFError):
            cli.run()
        assert "Goodbye" in capsys.readouterr().out

    def test_invalid_option_reprompts(self, signed_in, capsys):
        with signed_in, answers("99", cli.EXIT_KEY):
            cli.run()
        assert "Invalid option" in capsys.readouterr().out

    def test_an_action_blowing_up_does_not_kill_the_menu(self, signed_in, capsys):
        with patch.dict(cli.MENU_ACTIONS, {"1": ("Boom", lambda uid: 1 / 0)}):
            with signed_in, answers("1", cli.EXIT_KEY):
                cli.run()
        out = capsys.readouterr().out
        assert "Something went wrong" in out
        assert "Goodbye" in out

    def test_no_account_configured_explains_how_to_fix_it(self, db, capsys):
        """PORTFOLIO_USER unset must produce guidance, not a traceback."""
        from portfolio_tracker import config

        with patch.object(config, "PORTFOLIO_USER", None):
            cli.run()
        out = capsys.readouterr().out
        assert "PORTFOLIO_USER" in out

    def test_unknown_account_names_the_known_ones(self, user, capsys):
        from portfolio_tracker import config

        with patch.object(config, "PORTFOLIO_USER", "nobody@example.com"):
            cli.run()
        out = capsys.readouterr().out
        assert "No account found" in out
        assert "tester@example.com" in out

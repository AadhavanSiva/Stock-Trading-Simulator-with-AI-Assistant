"""The leaderboard, and the privacy rules it exists under.

The privacy tests are the point of this file. A ranking feature that leaks
is worse than no ranking feature, and "we only select the safe columns" is
the kind of thing that stays true until someone widens a SELECT.
"""
from decimal import Decimal

import pytest

from portfolio_tracker import operations
from portfolio_tracker.db import cursor
from portfolio_tracker.models import leaderboard, portfolio, stocks, users


@pytest.fixture
def seeded(user):
    stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
    return user


def value(user_id, total):
    """Give an account a recorded value, as a refresh would."""
    with cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO portfolio_value_history
                (user_id, cash, holdings_value, total_value)
            VALUES (%s, %s, 0, %s)
            """,
            (user_id, total, total),
        )


def join(user_id, name):
    return operations.set_leaderboard_participation(user_id, True, name=name)


class TestItIsOptIn:
    def test_a_new_account_is_not_listed(self, seeded):
        value(seeded, Decimal("60000"))
        assert operations.leaderboard_standings() == []

    def test_the_database_defaults_to_off(self, seeded):
        opted_in, name = users.get_leaderboard_settings(seeded)
        assert opted_in is False and name is None

    def test_joining_lists_you(self, seeded):
        value(seeded, Decimal("60000"))
        join(seeded, "Racer")
        assert [row.name for row in operations.leaderboard_standings()] == ["Racer"]

    def test_leaving_removes_you(self, seeded):
        value(seeded, Decimal("60000"))
        join(seeded, "Racer")
        operations.set_leaderboard_participation(seeded, False)
        assert operations.leaderboard_standings() == []

    def test_leaving_keeps_the_name_for_next_time(self, seeded):
        join(seeded, "Racer")
        operations.set_leaderboard_participation(seeded, False)
        assert users.get_leaderboard_settings(seeded) == (False, "Racer")

    def test_the_database_refuses_opted_in_with_no_name(self, seeded):
        """Otherwise the view needs a fallback, and the obvious fallback is
        the email address this feature exists to keep off the page."""
        with pytest.raises(Exception):
            with cursor(commit=True) as cur:
                cur.execute(
                    "UPDATE users SET leaderboard_opt_in = TRUE, "
                    "leaderboard_name = NULL WHERE id = %s",
                    (seeded,),
                )


class TestPrivacy:
    def test_the_page_never_shows_an_email_address(self, client, seeded, other_user):
        value(other_user, Decimal("90000"))
        operations.set_leaderboard_participation(other_user, True, name="Rival")
        value(seeded, Decimal("60000"))
        join(seeded, "Racer")

        page = client.get("/leaderboard").data
        assert b"other@example.com" not in page
        assert b"tester@example.com" not in page

    def test_the_page_never_shows_a_google_display_name(self, client, seeded, other_user):
        value(other_user, Decimal("90000"))
        operations.set_leaderboard_participation(other_user, True, name="Rival")
        page = client.get("/leaderboard").data
        assert b"Other" not in page.split(b"<footer")[0].replace(b"Others", b"")

    def test_the_page_never_shows_holdings_or_trades(self, client, seeded, other_user):
        stocks.upsert_stock("SECRET", "Secret Corp", Decimal("500"))
        portfolio.record_purchase(other_user, "SECRET", "Secret Corp",
                                  Decimal("500"), Decimal("2"))
        value(other_user, Decimal("90000"))
        operations.set_leaderboard_participation(other_user, True, name="Rival")

        page = client.get("/leaderboard").data
        assert b"SECRET" not in page
        assert b"Secret Corp" not in page

    def test_the_page_never_shows_another_player_s_cash(self, client, seeded, other_user):
        with cursor(commit=True) as cur:
            cur.execute("UPDATE users SET cash = 12345.67 WHERE id = %s", (other_user,))
        value(other_user, Decimal("90000"))
        operations.set_leaderboard_participation(other_user, True, name="Rival")
        assert b"12,345.67" not in client.get("/leaderboard").data

    def test_the_standings_carry_only_safe_fields(self, seeded):
        value(seeded, Decimal("60000"))
        join(seeded, "Racer")
        row = operations.leaderboard_standings()[0]
        assert set(row._fields) == {
            "rank", "name", "total_value", "percent_return", "is_you"
        }

    def test_an_opted_out_account_is_invisible_to_others(self, client, seeded, other_user):
        value(other_user, Decimal("99999"))     # would be top, but private
        value(seeded, Decimal("60000"))
        join(seeded, "Racer")
        page = client.get("/leaderboard").data
        assert b"99,999" not in page

    def test_a_name_cannot_be_an_email_address(self, seeded):
        with pytest.raises(operations.ValidationError, match="nickname"):
            join(seeded, "me@example.com")

    def test_a_name_must_be_chosen_to_join(self, seeded):
        with pytest.raises(operations.ValidationError):
            operations.set_leaderboard_participation(seeded, True, name="   ")

    def test_a_name_has_a_length_limit(self, seeded):
        with pytest.raises(operations.ValidationError, match="too long"):
            join(seeded, "x" * (operations.LEADERBOARD_NAME_MAX + 1))

    def test_two_players_cannot_share_a_name(self, seeded, other_user):
        join(seeded, "Racer")
        with pytest.raises(operations.ValidationError, match="already using"):
            join(other_user, "racer")

    def test_keeping_your_own_name_is_not_a_clash(self, seeded):
        join(seeded, "Racer")
        join(seeded, "Racer")
        assert users.get_leaderboard_settings(seeded)[1] == "Racer"


class TestRanking:
    def test_higher_value_ranks_first(self, seeded, other_user):
        value(seeded, Decimal("60000"))
        value(other_user, Decimal("90000"))
        join(seeded, "Lower")
        operations.set_leaderboard_participation(other_user, True, name="Higher")

        rows = operations.leaderboard_standings()
        assert [row.name for row in rows] == ["Higher", "Lower"]
        assert [row.rank for row in rows] == [1, 2]

    def test_ties_share_a_rank(self, seeded, other_user):
        """Two accounts worth the same are level; breaking it by id would
        invent an order out of who signed up first."""
        value(seeded, Decimal("60000"))
        value(other_user, Decimal("60000"))
        join(seeded, "A")
        operations.set_leaderboard_participation(other_user, True, name="B")
        assert {row.rank for row in operations.leaderboard_standings()} == {1}

    def test_only_the_newest_value_counts(self, seeded):
        value(seeded, Decimal("10000"))
        value(seeded, Decimal("70000"))
        assert operations.leaderboard_standings.__name__     # readability guard
        join(seeded, "Racer")
        assert operations.leaderboard_standings()[0].total_value == Decimal("70000")

    def test_the_return_is_against_the_opening_balance(self, seeded):
        value(seeded, Decimal("60000"))      # started at 50,000
        join(seeded, "Racer")
        assert operations.leaderboard_standings()[0].percent_return == 20

    def test_your_own_row_is_marked(self, seeded, other_user):
        value(seeded, Decimal("60000"))
        value(other_user, Decimal("90000"))
        join(seeded, "Me")
        operations.set_leaderboard_participation(other_user, True, name="Them")

        rows = operations.leaderboard_standings(user_id=seeded)
        assert [row.is_you for row in rows] == [False, True]

    def test_an_account_with_no_value_is_not_ranked(self, seeded):
        join(seeded, "Racer")
        assert operations.leaderboard_standings() == []


class TestYourOwnStanding:
    def test_you_see_it_without_opting_in(self, seeded, other_user):
        """The question "where would I come" is what makes the choice to
        join an informed one."""
        value(other_user, Decimal("90000"))
        operations.set_leaderboard_participation(other_user, True, name="Rival")
        value(seeded, Decimal("60000"))

        standing = operations.my_standing(seeded)
        assert standing.opted_in is False
        assert standing.rank == 2
        assert standing.total_value == Decimal("60000")

    def test_it_counts_only_opted_in_accounts(self, seeded, other_user):
        """A private account is not silently occupying a public slot."""
        value(other_user, Decimal("90000"))       # private, ranks above
        value(seeded, Decimal("60000"))
        assert operations.my_standing(seeded).rank == 1

    def test_it_is_none_before_the_account_is_valued(self, seeded):
        assert operations.my_standing(seeded) is None

    def test_it_reports_the_participant_count(self, seeded, other_user):
        value(seeded, Decimal("60000"))
        value(other_user, Decimal("90000"))
        join(seeded, "Me")
        operations.set_leaderboard_participation(other_user, True, name="Them")
        assert operations.my_standing(seeded).participants == 2

    def test_it_carries_the_time_it_was_valued(self, seeded):
        value(seeded, Decimal("60000"))
        assert operations.my_standing(seeded).as_of is not None


class TestItIsCachedNotRecomputed:
    def test_it_reads_stored_values_rather_than_pricing_holdings(self, seeded):
        """The whole page must not trigger a market-data pass. The autouse
        no_real_market_data fixture turns any such call into an error."""
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        value(seeded, Decimal("60000"))
        join(seeded, "Racer")
        assert operations.leaderboard_standings()[0].total_value == Decimal("60000")

    def test_a_stale_value_is_used_rather_than_recomputed(self, seeded):
        """Changing the price does not move the standing until a refresh
        records a new sample — the page says it is as of the last refresh,
        and this is what makes that true."""
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("10"))
        value(seeded, Decimal("60000"))
        join(seeded, "Racer")
        stocks.update_stock_price("AAPL", Decimal("999"))
        assert operations.leaderboard_standings()[0].total_value == Decimal("60000")


class TestThePages:
    def test_the_leaderboard_needs_a_signed_in_account(self, db):
        import web as web_module

        web_module.app.config.update(TESTING=True)
        with web_module.app.test_client() as anonymous:
            assert anonymous.get("/leaderboard").status_code == 302

    def test_it_says_what_is_shown(self, client, seeded):
        page = client.get("/leaderboard").data
        assert b"never real names, email addresses, holdings, trades or cash" in page

    def test_an_empty_leaderboard_explains_itself(self, client, seeded):
        assert b"Nobody has joined yet" in client.get("/leaderboard").data

    def test_it_shows_your_rank_when_you_have_not_joined(self, client, seeded):
        value(seeded, Decimal("60000"))
        page = client.get("/leaderboard").data
        assert b"Where you would come if you joined" in page

    def test_the_account_page_offers_the_toggle(self, client, seeded):
        page = client.get("/account").data
        assert b"Show me on the leaderboard" in page
        assert b"name or email address" in page

    def test_joining_through_the_form(self, client, seeded):
        value(seeded, Decimal("60000"))
        client.post("/account/leaderboard",
                    data={"opt_in": "on", "leaderboard_name": "Racer"})
        assert users.get_leaderboard_settings(seeded) == (True, "Racer")

    def test_leaving_through_the_form(self, client, seeded):
        join(seeded, "Racer")
        client.post("/account/leaderboard", data={"leaderboard_name": "Racer"})
        assert users.get_leaderboard_settings(seeded)[0] is False

    def test_a_bad_name_is_refused_with_a_message(self, client, seeded):
        response = client.post("/account/leaderboard",
                               data={"opt_in": "on",
                                     "leaderboard_name": "me@example.com"},
                               follow_redirects=True)
        assert b"nickname" in response.data
        assert users.get_leaderboard_settings(seeded)[0] is False

    def test_the_toggle_needs_a_csrf_token(self, db):
        import web as web_module

        web_module.app.config.update(TESTING=True)
        with web_module.app.test_client() as anonymous:
            assert anonymous.post("/account/leaderboard",
                                  data={"opt_in": "on"}).status_code == 400


class TestDeletion:
    def test_deleting_an_account_removes_it_from_the_leaderboard(self, seeded):
        value(seeded, Decimal("60000"))
        join(seeded, "Racer")
        assert leaderboard.participant_count() == 1

        operations.delete_account(seeded, "DELETE")
        assert leaderboard.participant_count() == 0
        assert operations.leaderboard_standings() == []

    def test_no_orphaned_value_rows_survive(self, seeded):
        """The leaderboard entry is the user row plus its value samples, so
        the cascade has to reach both."""
        value(seeded, Decimal("60000"))
        join(seeded, "Racer")
        operations.delete_account(seeded, "DELETE")

        with cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM portfolio_value_history WHERE user_id = %s",
                (seeded,),
            )
            assert cur.fetchone()[0] == 0

    def test_the_name_is_released_for_someone_else(self, seeded, other_user):
        join(seeded, "Racer")
        operations.delete_account(seeded, "DELETE")
        join(other_user, "Racer")       # must not raise
        assert users.get_leaderboard_settings(other_user)[1] == "Racer"

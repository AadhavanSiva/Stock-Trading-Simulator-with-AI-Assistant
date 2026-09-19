"""Taking an account's data out, and ending the account."""
import json
from decimal import Decimal

import pytest

from portfolio_tracker import operations
from portfolio_tracker.db import cursor
from portfolio_tracker.models import portfolio, stocks, users


@pytest.fixture
def seeded(user):
    stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("150"))
    stocks.upsert_stock("MSFT", "Microsoft Corp.", Decimal("400"))
    return user


@pytest.fixture
def traded(seeded):
    """An account with a position, a closed position and some history."""
    portfolio.record_purchase(seeded, "AAPL", "Apple Inc.", Decimal("100"), Decimal("10"))
    portfolio.record_purchase(seeded, "MSFT", "Microsoft Corp.", Decimal("380"), Decimal("5"))
    portfolio.record_sale(seeded, "MSFT", Decimal("400"), Decimal("5"))
    return seeded


def count(table, user_id):
    with cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s", (user_id,))
        return cur.fetchone()[0]


class TestExport:
    def test_it_carries_the_account_details(self, traded):
        data = operations.export_account(traded)
        assert data["account"]["email"] == "tester@example.com"
        assert data["account"]["display_name"] == "Tester"

    def test_it_carries_the_holdings(self, traded):
        holdings = operations.export_account(traded)["holdings"]
        assert [row["symbol"] for row in holdings] == ["AAPL"]
        assert holdings[0]["shares"] == "10"
        assert holdings[0]["average_cost"] == "100"

    def test_it_carries_the_whole_trade_history(self, traded):
        """Every trade, not just the first page of them."""
        for _ in range(40):
            portfolio.record_purchase(traded, "AAPL", "Apple Inc.",
                                      Decimal("100"), Decimal("1"))
        data = operations.export_account(traded)
        assert len(data["trades"]) == 43

    def test_it_reports_realized_gains(self, traded):
        data = operations.export_account(traded)
        assert data["totals"]["realized_gain"] == "100"   # (400-380)*5

    def test_money_survives_the_round_trip_exactly(self, seeded):
        """Decimals go out as strings; float() would corrupt a cost basis."""
        portfolio.record_purchase(seeded, "AAPL", "Apple Inc.",
                                  Decimal("164.20"), Decimal("3"))
        data = operations.export_account(seeded)
        assert data["holdings"][0]["average_cost"] == "164.20"
        assert isinstance(data["holdings"][0]["average_cost"], str)

    def test_it_is_json_serializable(self, traded):
        """Decimals and timestamps must already be plain values."""
        text = json.dumps(operations.export_account(traded))
        assert json.loads(text)["format_version"] == 1

    def test_it_leaves_out_shared_market_data(self, traded):
        """Price history belongs to everyone, not to this account."""
        data = operations.export_account(traded)
        assert "price_history" not in data and "prices" not in data

    def test_it_holds_only_this_account_s_data(self, traded, other_user):
        portfolio.record_purchase(other_user, "MSFT", "Microsoft Corp.",
                                  Decimal("380"), Decimal("2"))
        text = json.dumps(operations.export_account(traded))
        assert "other@example.com" not in text

    def test_an_unknown_account_is_refused(self, db):
        with pytest.raises(operations.UnknownUser):
            operations.export_account(999999)

    def test_the_route_serves_it_as_a_download(self, client, traded):
        response = client.get("/account/export")
        assert response.status_code == 200
        assert response.mimetype == "application/json"
        assert "attachment" in response.headers["Content-Disposition"]
        assert ".json" in response.headers["Content-Disposition"]

    def test_the_download_is_never_cached(self, client, traded):
        assert client.get("/account/export").headers["Cache-Control"] == "no-store"

    def test_the_download_needs_a_signed_in_account(self, db):
        import web as web_module

        web_module.app.config.update(TESTING=True)
        with web_module.app.test_client() as anonymous:
            assert anonymous.get("/account/export").status_code == 302


class TestTheTypedConfirmation:
    def test_the_exact_word_deletes(self, traded):
        assert operations.delete_account(traded, "DELETE") == "tester@example.com"

    def test_it_is_not_case_sensitive(self, traded):
        assert operations.delete_account(traded, "delete")

    def test_surrounding_space_is_forgiven(self, traded):
        assert operations.delete_account(traded, "  DELETE  ")

    @pytest.mark.parametrize("typed", ["", "   ", "yes", "DELET", "delete my account",
                                       "DELETE ACCOUNT", "confirm", None])
    def test_anything_else_is_refused(self, traded, typed):
        with pytest.raises(operations.ValidationError):
            operations.delete_account(traded, typed)

    def test_a_refusal_deletes_nothing(self, traded):
        with pytest.raises(operations.ValidationError):
            operations.delete_account(traded, "yes")
        assert users.get_by_id(traded) is not None
        assert count("trades", traded) == 3

    def test_the_guard_does_not_live_only_in_the_template(self, client, traded):
        """A POST straight at the endpoint still has to carry the word."""
        client.post("/account/delete", data={})
        assert users.get_by_id(traded) is not None

    def test_the_message_says_what_to_type(self, traded):
        with pytest.raises(operations.ValidationError, match="DELETE"):
            operations.delete_account(traded, "nope")

    def test_the_message_says_nothing_was_deleted(self, traded):
        with pytest.raises(operations.ValidationError, match="[Nn]othing has been deleted"):
            operations.delete_account(traded, "nope")


class TestDeletion:
    def test_the_account_is_gone(self, traded):
        operations.delete_account(traded, "DELETE")
        assert users.get_by_id(traded) is None

    def test_the_positions_go_with_it(self, traded):
        operations.delete_account(traded, "DELETE")
        assert count("portfolio", traded) == 0

    def test_the_trade_log_goes_with_it(self, traded):
        """Append-only stops a rewrite; it must not stop an erasure."""
        assert count("trades", traded) == 3
        operations.delete_account(traded, "DELETE")
        assert count("trades", traded) == 0

    def test_the_rate_limit_rows_go_with_it(self, traded):
        from portfolio_tracker.models import assistant_usage

        assistant_usage.reserve_question(traded, limit=5, window_seconds=600)
        operations.delete_account(traded, "DELETE")
        assert count("assistant_requests", traded) == 0

    def test_no_orphaned_rows_are_left_anywhere(self, traded):
        """Whatever tables reference users, none may survive the delete."""
        operations.delete_account(traded, "DELETE")
        with cursor() as cur:
            cur.execute("""
                SELECT DISTINCT tc.table_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND ccu.table_name = 'users'
            """)
            referencing = [row[0] for row in cur.fetchall()]
        assert referencing, "expected some table to reference users"
        for table in referencing:
            assert count(table, traded) == 0, f"{table} kept a row"

    def test_shared_market_data_is_left_alone(self, traded):
        """Prices are not this account's to take away from everyone else."""
        operations.delete_account(traded, "DELETE")
        with cursor() as cur:
            cur.execute("SELECT count(*) FROM stocks")
            assert cur.fetchone()[0] == 2

    def test_another_account_is_untouched(self, traded, other_user):
        portfolio.record_purchase(other_user, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("4"))
        operations.delete_account(traded, "DELETE")
        assert users.get_by_id(other_user) is not None
        assert count("portfolio", other_user) == 1
        assert count("trades", other_user) == 1

    def test_deleting_twice_is_refused_rather_than_silent(self, traded):
        operations.delete_account(traded, "DELETE")
        with pytest.raises(operations.UnknownUser):
            operations.delete_account(traded, "DELETE")

    def test_signing_in_again_opens_a_fresh_account(self, traded):
        """The same Google identity, but nothing carried over."""
        operations.delete_account(traded, "DELETE")
        account = users.upsert_from_google("test-sub-1", "tester@example.com", "Tester")
        assert account[0] != traded
        assert account[4] == users.starting_cash()
        assert count("trades", account[0]) == 0

    def test_it_all_happens_in_one_transaction(self, traded, monkeypatch):
        """A failure partway must leave the account exactly as it was."""
        from portfolio_tracker.models import users as users_model

        real_cursor = users_model.cursor

        class Boom(Exception):
            pass

        def exploding_cursor(*args, **kwargs):
            manager = real_cursor(*args, **kwargs)

            class Wrapper:
                def __enter__(self):
                    self.cur = manager.__enter__()
                    return self

                def execute(self, sql, params=None):
                    self.cur.execute(sql, params)
                    if "DELETE FROM users" in sql:
                        raise Boom("failed after the delete, before the commit")

                def fetchone(self):
                    return self.cur.fetchone()

                def __exit__(self, *exc):
                    return manager.__exit__(*exc)

            return Wrapper()

        monkeypatch.setattr(users_model, "cursor", exploding_cursor)
        with pytest.raises(Boom):
            operations.delete_account(traded, "DELETE")

        monkeypatch.undo()
        assert users.get_by_id(traded) is not None
        assert count("trades", traded) == 3
        assert count("portfolio", traded) == 1


class TestThePages:
    def test_the_account_page_offers_both(self, client, traded):
        page = client.get("/account").data
        assert b"Download my data" in page
        assert b"Delete this account" in page

    def test_the_account_page_warns_it_cannot_be_undone(self, client, traded):
        assert b"cannot be undone" in client.get("/account").data

    def test_the_account_page_asks_for_the_typed_word(self, client, traded):
        page = client.get("/account").data
        assert b'name="confirmation"' in page
        assert operations.DELETE_CONFIRMATION.encode() in page

    def test_a_wrong_word_says_so_and_keeps_the_account(self, client, traded):
        response = client.post("/account/delete",
                               data={"confirmation": "please"},
                               follow_redirects=True)
        assert b"Type" in response.data
        assert users.get_by_id(traded) is not None

    def test_deleting_signs_you_out(self, client, traded):
        client.post("/account/delete", data={"confirmation": "DELETE"})
        with client.session_transaction() as session:
            assert "user_id" not in session

    def test_deleting_shows_a_confirmation_page(self, client, traded):
        response = client.post("/account/delete",
                               data={"confirmation": "DELETE"},
                               follow_redirects=True)
        assert response.status_code == 200
        assert b"Your account is gone" in response.data
        assert b"tester@example.com" in response.data

    def test_the_confirmation_page_is_shown_only_once(self, client, traded):
        client.post("/account/delete", data={"confirmation": "DELETE"},
                    follow_redirects=True)
        assert client.get("/account/deleted").status_code == 302

    def test_the_confirmation_page_is_not_a_public_url(self, db):
        import web as web_module

        web_module.app.config.update(TESTING=True)
        with web_module.app.test_client() as anonymous:
            assert anonymous.get("/account/deleted").status_code == 302

    def test_a_cross_site_post_is_refused_before_anything_else(self, db):
        """No token, so CSRF turns it away ahead of the sign-in check."""
        import web as web_module

        web_module.app.config.update(TESTING=True)
        with web_module.app.test_client() as anonymous:
            assert anonymous.post("/account/delete",
                                  data={"confirmation": "DELETE"}).status_code == 400

    def test_deleting_needs_a_signed_in_account(self, db, monkeypatch):
        """And with a valid token, an anonymous caller still gets nowhere."""
        import web as web_module

        web_module.app.config.update(TESTING=True)
        monkeypatch.setitem(web_module.app.config, "WTF_CSRF_ENABLED", False)
        with web_module.app.test_client() as anonymous:
            assert anonymous.post("/account/delete",
                                  data={"confirmation": "DELETE"}).status_code == 302

    def test_a_get_cannot_delete(self, client, traded):
        """Another site could trigger a GET with an image tag."""
        assert client.get("/account/delete").status_code == 405
        assert users.get_by_id(traded) is not None

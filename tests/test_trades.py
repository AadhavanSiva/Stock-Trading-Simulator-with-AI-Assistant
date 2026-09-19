"""The append-only trade log: what it records, what it refuses, and the
realized gains derived from it."""
from decimal import Decimal

import pytest

from portfolio_tracker import operations
from portfolio_tracker.db import cursor
from portfolio_tracker.models import portfolio, stocks, trades


@pytest.fixture
def seeded(user):
    stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("100"))
    stocks.upsert_stock("MSFT", "Microsoft Corp.", Decimal("200"))
    return user


def buy(user_id, symbol="AAPL", shares="10", price="100"):
    return portfolio.record_purchase(
        user_id, symbol, "Test Co", Decimal(price), Decimal(shares)
    )


def rows(user_id):
    with cursor() as cur:
        cur.execute(
            "SELECT side, symbol, shares, price, total_value, cost_basis, backfilled "
            "FROM trades WHERE user_id = %s ORDER BY id",
            (user_id,),
        )
        return cur.fetchall()


class TestWhatIsRecorded:
    def test_a_buy_is_logged(self, seeded):
        buy(seeded, shares="10", price="100")
        (side, symbol, shares, price, total, basis, backfilled), = rows(seeded)
        assert (side, symbol) == ("buy", "AAPL")
        assert shares == 10 and price == 100
        assert total == Decimal("1000.00")
        assert backfilled is False

    def test_a_sell_is_logged(self, seeded):
        buy(seeded, shares="10", price="100")
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("4"))
        sell = rows(seeded)[1]
        assert sell[0] == "sell"
        assert sell[2] == 4 and sell[3] == 150
        assert sell[4] == Decimal("600.00")

    def test_a_buy_records_no_cost_basis(self, seeded):
        """A buy has not settled anything, so it has no basis to report."""
        buy(seeded)
        assert rows(seeded)[0][5] is None

    def test_a_sell_records_the_basis_it_was_priced_against(self, seeded):
        buy(seeded, shares="10", price="100")
        buy(seeded, shares="10", price="200")   # average is now 150
        portfolio.record_sale(seeded, "AAPL", Decimal("300"), Decimal("5"))
        assert rows(seeded)[2][5] == Decimal("150")

    def test_the_total_value_matches_the_cash_that_moved(self, seeded):
        """The ledger must agree with the balance, not approximate it."""
        from portfolio_tracker.models import users

        before = users.get_cash(seeded)
        buy(seeded, shares="3.3333", price="19.99")
        after = users.get_cash(seeded)
        assert rows(seeded)[0][4] == before - after

    def test_an_invalid_sale_logs_nothing(self, seeded):
        buy(seeded, shares="10")
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("99"))
        assert len(rows(seeded)) == 1

    def test_trades_are_scoped_to_their_account(self, seeded, other_user):
        buy(seeded)
        assert trades.count(seeded) == 1
        assert trades.count(other_user) == 0


class TestTheLogCannotDisagreeWithTheBalances:
    def test_a_failed_purchase_leaves_no_trade_behind(self, seeded):
        """The trade row and the cash move in one transaction, so a purchase
        refused for lack of funds must roll the row back with it."""
        with pytest.raises(operations.InsufficientFunds):
            buy(seeded, shares="1000", price="100")   # $100,000 against $50,000
        assert rows(seeded) == []

    def test_every_position_can_be_rebuilt_from_the_log(self, seeded):
        buy(seeded, shares="10", price="100")
        buy(seeded, shares="10", price="200")
        portfolio.record_sale(seeded, "AAPL", Decimal("300"), Decimal("6"))

        net = sum(
            (row[2] if row[0] == "buy" else -row[2]) for row in rows(seeded)
        )
        assert net == portfolio.get_holding(seeded, "AAPL")[0]


class TestImmutability:
    """Enforced by the database, not by the application's good manners."""

    def test_a_trade_cannot_be_updated(self, seeded):
        buy(seeded)
        with pytest.raises(Exception, match="append-only"):
            with cursor(commit=True) as cur:
                cur.execute("UPDATE trades SET price = 1 WHERE user_id = %s", (seeded,))

    def test_a_trade_cannot_be_deleted(self, seeded):
        buy(seeded)
        with pytest.raises(Exception, match="append-only"):
            with cursor(commit=True) as cur:
                cur.execute("DELETE FROM trades WHERE user_id = %s", (seeded,))

    def test_the_row_survives_the_attempt(self, seeded):
        buy(seeded, price="100")
        for sql in ("UPDATE trades SET price = 1 WHERE user_id = %s",
                    "DELETE FROM trades WHERE user_id = %s"):
            with pytest.raises(Exception):
                with cursor(commit=True) as cur:
                    cur.execute(sql, (seeded,))
        assert rows(seeded)[0][3] == 100

    def test_the_model_offers_no_way_to_rewrite_one(self):
        """Nothing in the module should tempt a caller into trying."""
        assert not [
            name for name in dir(trades)
            if any(word in name.lower() for word in ("update", "delete", "remove", "edit"))
        ]

    def test_the_erasure_flag_does_not_outlive_its_transaction(self, seeded):
        """A later query on the same pool must not inherit the exemption."""
        buy(seeded)
        with cursor(commit=True) as cur:
            cur.execute("SET LOCAL app.erasing_account = 'on'")
        with pytest.raises(Exception, match="append-only"):
            with cursor(commit=True) as cur:
                cur.execute("DELETE FROM trades WHERE user_id = %s", (seeded,))


class TestRealizedGains:
    def test_nothing_is_realized_until_something_is_sold(self, seeded):
        buy(seeded, shares="10", price="100")
        assert trades.realized_total(seeded) == 0

    def test_a_sale_realizes_the_difference_from_its_basis(self, seeded):
        buy(seeded, shares="10", price="100")
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("4"))
        assert trades.realized_total(seeded) == Decimal("200")   # (150-100)*4

    def test_a_loss_is_realized_too(self, seeded):
        buy(seeded, shares="10", price="100")
        portfolio.record_sale(seeded, "AAPL", Decimal("60"), Decimal("5"))
        assert trades.realized_total(seeded) == Decimal("-200")

    def test_realized_is_reported_per_position(self, seeded):
        buy(seeded, "AAPL", shares="10", price="100")
        buy(seeded, "MSFT", shares="10", price="200")
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("2"))
        portfolio.record_sale(seeded, "MSFT", Decimal("180"), Decimal("2"))

        realized = operations.realized_summary(seeded)
        assert realized.by_symbol["AAPL"] == Decimal("100")
        assert realized.by_symbol["MSFT"] == Decimal("-40")
        assert realized.total == Decimal("60")

    def test_a_position_sold_out_of_entirely_is_listed_as_closed(self, seeded):
        buy(seeded, "AAPL", shares="10", price="100")
        buy(seeded, "MSFT", shares="10", price="200")
        portfolio.record_sale(seeded, "MSFT", Decimal("250"), Decimal("10"))

        closed = operations.realized_summary(seeded).closed
        assert [row["symbol"] for row in closed] == ["MSFT"]
        assert closed[0]["realized"] == Decimal("500")
        assert closed[0]["shares_sold"] == 10

    def test_a_position_still_held_is_not_listed_as_closed(self, seeded):
        buy(seeded, shares="10", price="100")
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("4"))
        assert operations.realized_summary(seeded).closed == []

    def test_the_summary_carries_realized_per_holding(self, seeded):
        buy(seeded, shares="10", price="100")
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("4"))
        row, = operations.account_summary(seeded).rows
        assert row["realized"] == Decimal("200")

    def test_realized_and_unrealized_are_reported_separately(self, seeded):
        """Selling half at a profit must not restate what the rest is worth."""
        buy(seeded, shares="10", price="100")
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("5"))
        summary = operations.account_summary(seeded)
        assert summary.realized.total == Decimal("250")
        # 5 shares left, bought at 100, quoted at 100 in the fixture.
        assert summary.total_gain == Decimal("0")

    def test_one_account_cannot_see_another_s_realized_gains(self, seeded, other_user):
        buy(seeded, shares="10", price="100")
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("4"))
        assert operations.realized_summary(other_user).total == 0

    def test_the_reported_gain_uses_the_basis_the_sale_locked(self, seeded):
        """sell() must not price the gain from a basis read beforehand."""
        buy(seeded, shares="10", price="100")
        import portfolio_tracker.services.market_data as md
        from unittest.mock import patch

        with patch.object(md, "get_live_price", return_value=Decimal("150")):
            sale = operations.sell(seeded, "AAPL", Decimal("4"))
        assert sale.gain == Decimal("200.00")
        assert trades.realized_total(seeded) == sale.gain


class TestPaging:
    def test_an_empty_history_is_one_empty_page(self, seeded):
        log = operations.trade_history(seeded)
        assert log.total == 0 and log.pages == 1 and log.rows == []

    def test_pages_split_at_the_page_size(self, seeded):
        for _ in range(7):
            buy(seeded, shares="1", price="10")
        log = operations.trade_history(seeded, page=1, page_size=3)
        assert (log.total, log.pages, len(log.rows)) == (7, 3, 3)

    def test_the_last_page_holds_the_remainder(self, seeded):
        for _ in range(7):
            buy(seeded, shares="1", price="10")
        assert len(operations.trade_history(seeded, page=3, page_size=3).rows) == 1

    def test_newest_comes_first(self, seeded):
        buy(seeded, "AAPL", shares="1", price="10")
        buy(seeded, "MSFT", shares="1", price="10")
        assert operations.trade_history(seeded).rows[0].symbol == "MSFT"

    def test_no_trade_appears_on_two_pages(self, seeded):
        """Rows written in the same instant must still order deterministically."""
        for _ in range(10):
            buy(seeded, shares="1", price="10")
        seen = []
        for number in (1, 2, 3, 4, 5):
            seen += [row.id for row in
                     operations.trade_history(seeded, page=number, page_size=2).rows]
        assert len(seen) == len(set(seen)) == 10

    def test_a_page_past_the_end_clamps_to_the_last_one(self, seeded):
        buy(seeded)
        assert operations.trade_history(seeded, page=99).page == 1

    def test_a_page_below_one_clamps_up(self, seeded):
        buy(seeded)
        assert operations.trade_history(seeded, page=-5).page == 1

    def test_a_nonsense_page_falls_back_to_the_first(self, seeded):
        buy(seeded)
        assert operations.trade_history(seeded, page="; DROP TABLE trades").page == 1

    def test_recent_activity_is_capped(self, seeded):
        for _ in range(9):
            buy(seeded, shares="1", price="10")
        assert len(operations.recent_activity(seeded, limit=5)) == 5


class TestThePages:
    def test_the_portfolio_page_shows_recent_activity(self, client, seeded):
        buy(seeded, shares="3", price="100")
        page = client.get("/").data
        assert b"Recent activity" in page
        assert b"Bought" in page

    def test_the_activity_page_lists_the_trades(self, client, seeded):
        buy(seeded, shares="3", price="100")
        page = client.get("/trades").data
        assert b"AAPL" in page and b"Bought" in page

    def test_the_activity_page_shows_realized_gains(self, client, seeded):
        buy(seeded, shares="10", price="100")
        portfolio.record_sale(seeded, "AAPL", Decimal("150"), Decimal("4"))
        page = client.get("/trades").data
        assert b"Realized" in page and b"+200.00" in page

    def test_an_account_with_no_trades_is_told_so(self, client, seeded):
        assert b"No trades yet" in client.get("/trades").data

    def test_paging_controls_appear_only_when_needed(self, client, seeded):
        buy(seeded)
        assert b"Page 1 of" not in client.get("/trades").data

    def test_the_activity_page_needs_a_signed_in_account(self, db):
        import web as web_module

        web_module.app.config.update(TESTING=True)
        with web_module.app.test_client() as anonymous:
            assert anonymous.get("/trades").status_code == 302

    def test_one_account_cannot_read_another_s_activity(self, client, seeded, other_user):
        buy(other_user, "MSFT", shares="5", price="200")
        assert b"MSFT" not in client.get("/trades").data


class TestTradeTimes:
    """A time is shown in UTC by the server and restated locally by JS."""

    def test_the_server_says_which_zone_it_means(self, client, seeded):
        """An unlabelled time would be read as local and quietly be wrong."""
        buy(seeded)
        assert b"UTC" in client.get("/trades").data

    def test_each_time_carries_a_machine_readable_stamp(self, client, seeded):
        buy(seeded)
        assert b"data-utc=" in client.get("/trades").data

    def test_the_script_restates_them_locally(self):
        import os

        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "static", "app.js")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        assert "[data-utc]" in source
        assert "toLocaleString" in source

    def test_the_utc_text_survives_a_failure(self):
        """The fallback is the rendered text, so a bad stamp leaves it alone."""
        import os

        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "static", "app.js")
        with open(path, encoding="utf-8") as fh:
            block = fh.read().split("[data-utc]")[1][:600]
        assert "isNaN" in block and "return" in block

"""Stocks model tests."""
from decimal import Decimal

from portfolio_tracker.models import stocks


class TestUpsertStock:
    def test_inserts_a_new_stock(self, db):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("200"))
        assert stocks.get_all_stocks() == ["AAPL"]

    def test_re_upsert_refreshes_the_price(self, db):
        """Regression: ON CONFLICT DO NOTHING froze the price at its first value,
        so every gain/loss in the report was measured against a stale quote."""
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("200"))
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("999"))

        from portfolio_tracker.db import cursor

        with cursor() as cur:
            cur.execute("SELECT current_price FROM stocks WHERE symbol = 'AAPL'")
            assert cur.fetchone()[0] == Decimal("999")

    def test_null_lookup_does_not_wipe_a_known_price(self, db):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("200"))
        stocks.upsert_stock("AAPL", None, None)

        from portfolio_tracker.db import cursor

        with cursor() as cur:
            cur.execute("SELECT company_name, current_price FROM stocks WHERE symbol='AAPL'")
            name, price = cur.fetchone()
        assert name == "Apple Inc."
        assert price == Decimal("200")

    def test_does_not_create_duplicates(self, db):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("200"))
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("201"))
        assert stocks.get_all_stocks() == ["AAPL"]


class TestUpdateStockPrice:
    def test_updates_an_existing_symbol(self, db):
        stocks.upsert_stock("AAPL", "Apple Inc.", Decimal("200"))
        assert stocks.update_stock_price("AAPL", Decimal("250")) is True

    def test_reports_an_unknown_symbol(self, db):
        """rowcount lets the caller tell 'updated' from 'that symbol isn't here'."""
        assert stocks.update_stock_price("NOPE", Decimal("1")) is False

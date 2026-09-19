"""The assistant's rate limit, counted in PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor

import pytest

import web as web_module
from portfolio_tracker import operations
from portfolio_tracker.db import cursor
from portfolio_tracker.models import assistant_usage


def recorded(user_id=None):
    with cursor() as cur:
        if user_id is None:
            cur.execute("SELECT count(*) FROM assistant_requests")
        else:
            cur.execute("SELECT count(*) FROM assistant_requests WHERE user_id = %s", (user_id,))
        return cur.fetchone()[0]


def asked_seconds_ago(user_id, seconds, times=1):
    with cursor(commit=True) as cur:
        for _ in range(times):
            cur.execute(
                "INSERT INTO assistant_requests (user_id, asked_at) "
                "VALUES (%s, now() - make_interval(secs => %s))",
                (user_id, seconds),
            )


class TestReserving:
    def test_allows_up_to_the_limit_then_says_how_long_to_wait(self, user):
        results = [assistant_usage.reserve_question(user, 3, 600) for _ in range(4)]
        assert results[:3] == [0, 0, 0]
        assert 590 <= results[3] <= 600
        assert recorded(user) == 3                 # the refused one is not counted

    def test_the_wait_counts_down_from_the_oldest_question(self, user):
        asked_seconds_ago(user, 550)
        asked_seconds_ago(user, 10)
        wait = assistant_usage.reserve_question(user, 2, 600)
        assert 49 <= wait <= 51

    def test_questions_older_than_the_window_do_not_count_and_are_cleared(self, user):
        asked_seconds_ago(user, 601, times=5)
        assert assistant_usage.reserve_question(user, 2, 600) == 0
        assert recorded(user) == 1

    def test_accounts_are_limited_separately(self, user, other_user):
        assistant_usage.reserve_question(user, 1, 600)
        assert assistant_usage.reserve_question(user, 1, 600) > 0
        assert assistant_usage.reserve_question(other_user, 1, 600) == 0

    def test_a_zero_limit_allows_nothing(self, user):
        assert assistant_usage.reserve_question(user, 0, 600) > 0
        assert recorded(user) == 0

    def test_deleting_an_account_removes_its_rows(self, user):
        assistant_usage.reserve_question(user, 5, 600)
        with cursor(commit=True) as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user,))
        assert recorded() == 0


class TestSharedAcrossProcesses:
    def test_simultaneous_requests_cannot_exceed_the_limit(self, user):
        """Each call uses its own database connection, as separate worker
        processes would. The advisory lock makes check-then-insert atomic."""
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: assistant_usage.reserve_question(user, 5, 600), range(30)))
        assert results.count(0) == 5
        assert recorded(user) == 5

    def test_questions_recorded_by_another_process_count_here(self, client, user, monkeypatch):
        """Nothing is kept in this process: rows written elsewhere (another
        worker, or before a restart) are what the web app enforces."""
        monkeypatch.setattr(web_module, "ASSISTANT_LIMIT", 3)
        asked_seconds_ago(user, 5, times=3)
        response = client.post("/api/assistant", json={"question": "Anything?"})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) >= 590

    def test_the_web_app_keeps_no_in_memory_counter(self):
        assert not hasattr(web_module, "_asked")

    def test_the_operation_uses_the_database(self, user):
        assert operations.assistant_wait_seconds(user, 1, 600) == 0
        assert recorded(user) == 1
        assert operations.assistant_wait_seconds(user, 1, 600) > 0

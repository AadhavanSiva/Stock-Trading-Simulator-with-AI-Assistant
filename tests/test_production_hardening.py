"""Things that only matter once the app is on the public internet.

Also the intraday fetch window, which is not a production concern but is
the same shape of bug: a rule enforced in one layer and quietly absent
from the one next to it.
"""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from conftest import FakeResponse, asset_payload

from portfolio_tracker import config
from portfolio_tracker.models import history
from portfolio_tracker.services import market_data


@pytest.fixture
def anonymous(db):
    import web as web_module

    web_module.app.config.update(TESTING=True)
    with web_module.app.test_client() as c:
        yield c


class TestTheIntradayFetchWindow:
    """Regression: `period="1d"` became a literal now-minus-24-hours, so
    from Friday evening to Monday's open it fetched nothing at all and the
    1D chart could never fill. The read path had already solved this — see
    history.get_intraday_sessions — but a wall-clock *fetch* window leaves
    it nothing to read.
    """

    def test_one_day_reaches_past_a_weekend(self):
        start = market_data._start_for("1d", intraday=True)
        span = datetime.now(timezone.utc) - start
        assert span > timedelta(days=3), (
            "a 1D fetch must clear a Friday-to-Monday gap, "
            f"but only reaches back {span}")

    def test_five_day_reaches_past_a_long_weekend(self):
        start = market_data._start_for("5d", intraday=True)
        span = datetime.now(timezone.utc) - start
        assert span > timedelta(days=10)

    def test_the_daily_window_is_left_alone(self):
        """Only session-based ranges over-reach; a 6-month chart means six
        months."""
        start = market_data._start_for("6mo")
        span = datetime.now(timezone.utc) - start
        assert timedelta(days=180) < span < timedelta(days=200)

    def test_a_sunday_night_fetch_still_finds_the_last_session(self):
        """The shape the bug actually took, driven through the real call."""
        friday = datetime.now(timezone.utc) - timedelta(days=3)
        rows = [{"t": (friday + timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "o": 1, "h": 1, "l": 1, "c": 1 + i, "v": 10} for i in range(4)]
        asked = {}

        def get(url, params=None, **kwargs):
            if "/v2/assets/" in url:
                return FakeResponse(200, asset_payload())
            asked.update(params or {})
            begin = datetime.strptime(params["start"], "%Y-%m-%dT%H:%M:%SZ")
            begin = begin.replace(tzinfo=timezone.utc)
            visible = [r for r in rows
                       if datetime.strptime(r["t"], "%Y-%m-%dT%H:%M:%SZ")
                       .replace(tzinfo=timezone.utc) >= begin]
            return FakeResponse(200, {"bars": {"AAPL": visible},
                                      "next_page_token": None})

        with patch.object(market_data._session, "get", side_effect=get):
            bars = market_data.get_intraday("AAPL", period="1d", interval="5m")
        assert bars, f"fetched nothing; window started {asked.get('start')}"

    def test_the_fetch_side_points_at_the_read_side_constraint(self):
        """So the next person to touch it meets the reason, not just the
        number."""
        import inspect

        source = inspect.getsource(market_data)
        window = source[source.index("INTRADAY_LOOKBACK_DAYS"):]
        assert "get_intraday_sessions" in source[:source.index("INTRADAY_LOOKBACK_DAYS")] \
            or "get_intraday_sessions" in window[:1200]


class TestTheAllTimeClaim:
    def test_the_provider_start_is_recorded(self):
        assert market_data.EARLIEST_AVAILABLE.year >= 2020

    def test_max_reaches_the_provider_start(self):
        start = market_data._start_for("max")
        assert start.year <= market_data.EARLIEST_AVAILABLE.year

    def test_no_comment_still_claims_2016(self):
        import inspect

        assert "starts in 2016" not in inspect.getsource(market_data)


class TestTheHealthEndpoint:
    def test_it_is_public(self, anonymous):
        assert anonymous.get("/healthz").status_code == 200

    def test_it_reports_a_commit(self, anonymous):
        payload = anonymous.get("/healthz").get_json()
        assert "commit" in payload and payload["commit"]

    def test_it_says_whether_market_data_is_configured(self, anonymous):
        payload = anonymous.get("/healthz").get_json()
        assert payload["market_data_configured"] is True   # conftest pins fakes

    def test_it_touches_no_database(self, anonymous, monkeypatch):
        """It has to answer while the database is asleep or unreachable,
        or it cannot tell you the app is up when you most need to know."""
        from portfolio_tracker import db

        def refuse(*args, **kwargs):
            raise AssertionError("/healthz opened a database connection")

        monkeypatch.setattr(db, "get_connection", refuse)
        assert anonymous.get("/healthz").status_code == 200

    def test_it_leaks_no_secrets(self, anonymous):
        body = anonymous.get("/healthz").get_data(as_text=True)
        for secret in (config.ALPACA_API_KEY_ID, config.ALPACA_API_SECRET_KEY):
            assert secret not in body


class TestSecurityHeaders:
    @pytest.mark.parametrize("header,expected", [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ])
    def test_they_are_set(self, anonymous, header, expected):
        assert anonymous.get("/").headers.get(header) == expected

    def test_a_content_security_policy_is_set(self, anonymous):
        policy = anonymous.get("/").headers.get("Content-Security-Policy")
        assert policy and "default-src 'self'" in policy

    def test_scripts_may_not_be_inline(self, anonymous):
        """The half of a CSP that actually stops an injected payload."""
        policy = anonymous.get("/").headers["Content-Security-Policy"]
        script = [d for d in policy.split(";") if d.strip().startswith("script-src")][0]
        assert "unsafe-inline" not in script
        assert "unsafe-eval" not in script

    def test_framing_is_refused_two_ways(self, anonymous):
        headers = anonymous.get("/").headers
        assert headers.get("X-Frame-Options") == "DENY"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

    def test_hsts_is_absent_without_a_proxy(self, anonymous, monkeypatch):
        """Setting it on plain local http would pin a developer's browser
        to https for 127.0.0.1 across every project on the machine."""
        monkeypatch.setattr(config, "BEHIND_HTTPS_PROXY", False)
        assert "Strict-Transport-Security" not in anonymous.get("/").headers

    def test_hsts_is_present_behind_one(self, anonymous, monkeypatch):
        monkeypatch.setattr(config, "BEHIND_HTTPS_PROXY", True)
        value = anonymous.get("/").headers.get("Strict-Transport-Security")
        assert value and "max-age=31536000" in value

    def test_they_are_on_error_pages_too(self, anonymous):
        """A 404 is still a page an attacker can aim a browser at."""
        assert anonymous.get("/nope").headers.get("X-Frame-Options") == "DENY"


class TestTheSessionCookie:
    def test_samesite_is_stated_not_inherited(self):
        import web as web_module

        assert web_module.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    def test_it_is_httponly(self):
        import web as web_module

        assert web_module.app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_lax_still_admits_the_oauth_callback(self):
        """Google returns the user by a top-level GET, which Lax allows and
        Strict would not — signing in would break silently."""
        import web as web_module

        assert web_module.app.config["SESSION_COOKIE_SAMESITE"] != "Strict"

    def test_the_flags_reach_a_real_response(self, anonymous, monkeypatch):
        monkeypatch.setattr(config, "BEHIND_HTTPS_PROXY", True)
        import web as web_module

        monkeypatch.setitem(web_module.app.config, "SESSION_COOKIE_SECURE", True)
        cookie = anonymous.get("/login?next=/balance").headers.get("Set-Cookie", "")
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie


class TestUnreadableBarsAreNotSilentlyEmpty:
    """The recurring shape: a failure leaving by the same door as "nothing
    to report". If Alpaca changed its timestamp format, every bar would be
    skipped and the loaders would cheerfully report "0 new days" during
    what is really a total parsing outage.
    """

    def _bars_response(self, timestamps):
        return {"bars": {"AAPL": [
            {"t": t, "o": 1, "h": 1, "l": 1, "c": 1, "v": 10} for t in timestamps
        ]}, "next_page_token": None}

    def _routed(self, payload):
        def get(url, **kwargs):
            if "/v2/assets/" in url:
                return FakeResponse(200, asset_payload())
            return FakeResponse(200, payload)
        return patch.object(market_data._session, "get", side_effect=get)

    def test_all_bars_unreadable_raises(self):
        payload = self._bars_response(["not-a-timestamp", "also-not-one"])
        with self._routed(payload):
            with pytest.raises(Exception, match="didn't respond|unavailable"):
                market_data.get_price_history("AAPL")

    def test_it_is_logged_as_a_format_problem(self, caplog):
        payload = self._bars_response(["nonsense"])
        with caplog.at_level("ERROR"), self._routed(payload):
            with pytest.raises(Exception):
                market_data.get_price_history("AAPL")
        assert "none could be parsed" in caplog.text
        assert "timestamp format" in caplog.text

    def test_one_bad_bar_among_good_ones_is_only_skipped(self):
        """A single corrupt row should cost one bar, not the request."""
        payload = self._bars_response(
            ["2026-09-18T04:00:00Z", "broken", "2026-09-17T04:00:00Z"])
        with self._routed(payload):
            bars = market_data.get_price_history("AAPL")
        assert len(bars) == 2

    def test_a_genuinely_empty_range_is_still_empty(self):
        """Nothing was returned, so nothing failed to parse."""
        with self._routed({"bars": None, "next_page_token": None}):
            assert market_data.get_price_history("AAPL") == []

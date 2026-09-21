"""Finding a company by name rather than by ticker.

The point of the feature is the person who does not know that Apple is
AAPL. That makes ranking the substance and not a refinement: a plain
substring search for "apple" returns Maui Land & Pineapple, Pineapple
Financial and two leveraged Apple ETFs before Apple itself, and offering
a beginner a 2x derivative as the top hit is worse than offering nothing.
"""
from unittest.mock import patch

import pytest
from conftest import FakeResponse

from portfolio_tracker import operations
from portfolio_tracker.services import market_data

# A slice of the real catalogue, including the entries that actually
# caused trouble when this was measured against Alpaca.
CATALOGUE = [
    {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "tradable": True},
    {"symbol": "APLE", "name": "Apple Hospitality REIT, Inc.", "tradable": True},
    {"symbol": "AAPX", "name": "T-Rex 2X Long Apple Daily Target ETF", "tradable": True},
    {"symbol": "AAPY", "name": "Kurv Yield Premium Strategy Apple (AAPL) ETF",
     "tradable": True},
    {"symbol": "MLP", "name": "Maui Land & Pineapple Co.", "tradable": True},
    {"symbol": "PAPL", "name": "Pineapple Financial Inc.", "tradable": True},
    {"symbol": "BRK.A", "name": "Berkshire Hathaway Inc.", "tradable": True},
    {"symbol": "BRK.B", "name": "BERKSHIRE HATHAWAY Class B", "tradable": True},
    {"symbol": "WRB", "name": "W.R. Berkley Corporation", "tradable": True},
    {"symbol": "VOO", "name": "Vanguard S&P 500 ETF", "tradable": True},
    {"symbol": "MSFT", "name": "Microsoft Corporation Common Stock", "tradable": True},
    {"symbol": "DELISTED", "name": "Gone Away Corp", "tradable": False},
]


@pytest.fixture(autouse=True)
def catalogue():
    """Serve the slice above from the transport, and clear it afterwards."""
    def get(url, **kwargs):
        if "/v2/assets" in url and not url.rstrip("/").endswith("/v2/assets") is False:
            return FakeResponse(200, CATALOGUE)
        return FakeResponse(200, CATALOGUE)

    market_data.clear_quote_cache()
    with patch.object(market_data._session, "get", side_effect=get):
        yield
    market_data.clear_quote_cache()


def symbols(query, **kwargs):
    return [row["symbol"] for row in market_data.search_assets(query, **kwargs)]


class TestPartialMatches:
    def test_a_prefix_of_the_name_finds_the_company(self):
        assert "AAPL" in symbols("app")

    def test_four_letters_find_berkshire(self):
        """The example that motivated word-prefix matching: BRK.B does not
        contain "berk" anywhere in its symbol."""
        found = symbols("berk")
        assert "BRK.A" in found and "BRK.B" in found

    def test_a_partial_word_mid_name_still_matches(self):
        assert "VOO" in symbols("vanguard")

    def test_a_single_letter_is_accepted(self):
        assert symbols("a")

    def test_an_empty_query_returns_nothing(self):
        assert market_data.search_assets("") == []
        assert market_data.search_assets("   ") == []


class TestCaseInsensitivity:
    @pytest.mark.parametrize("query", ["apple", "APPLE", "ApPlE", "  Apple  "])
    def test_case_and_space_do_not_matter(self, query):
        assert symbols(query)[0] == "AAPL"

    def test_a_shouty_catalogue_name_is_still_matched_in_lower_case(self):
        """BRK.B's name arrives from Alpaca in capitals."""
        assert "BRK.B" in symbols("berkshire")


class TestRanking:
    def test_an_exact_ticker_wins(self):
        """Typing AAPL must not bury Apple under funds with AAPL in the
        name — two of which exist in the real catalogue."""
        assert symbols("AAPL")[0] == "AAPL"

    def test_an_exact_ticker_wins_in_lower_case_too(self):
        assert symbols("aapl")[0] == "AAPL"

    def test_the_company_outranks_products_named_after_it(self):
        """The bug this ranking exists for: substring order put a 2x
        leveraged ETF above Apple."""
        found = symbols("apple")
        assert found[0] == "AAPL"
        assert found.index("AAPL") < found.index("AAPX")
        assert found.index("AAPL") < found.index("AAPY")

    def test_a_word_boundary_beats_a_mid_word_match(self):
        """"Pineapple" contains "apple" but no word starts with it."""
        found = symbols("apple")
        assert found.index("AAPL") < found.index("MLP")
        assert found.index("APLE") < found.index("PAPL")

    def test_the_plainer_name_comes_first_within_a_tier(self):
        found = symbols("apple")
        assert found.index("AAPL") < found.index("APLE")

    def test_both_share_classes_are_returned_separately(self):
        """The risk called out before normalization: they must not merge."""
        found = market_data.search_assets("berkshire")
        brk = [r for r in found if r["symbol"].startswith("BRK")]
        assert {r["symbol"] for r in brk} == {"BRK.A", "BRK.B"}
        assert brk[0]["name"] != brk[1]["name"]

    def test_the_limit_is_respected(self):
        assert len(market_data.search_assets("a", limit=3)) == 3


class TestNormalization:
    def test_listing_boilerplate_is_trimmed_for_display(self):
        assert market_data.display_name("Apple Inc. Common Stock") == "Apple Inc."

    @pytest.mark.parametrize("name,expected", [
        ("BERKSHIRE HATHAWAY Class B", "BERKSHIRE HATHAWAY Class B"),
        ("Vanguard S&P 500 ETF", "Vanguard S&P 500 ETF"),
        ("Some Co Series A", "Some Co Series A"),
        ("Thing Warrant", "Thing Warrant"),
    ])
    def test_distinguishing_words_are_never_stripped(self, name, expected):
        """Class, Series, ETF and Warrant tell two instruments apart."""
        assert market_data.display_name(name) == expected

    def test_casing_is_left_alone(self):
        """Title-casing would wreck S&P, ETF and JPMorgan."""
        assert market_data.display_name("BERKSHIRE HATHAWAY Class B").isupper() is False
        assert "S&P" in market_data.display_name("Vanguard S&P 500 ETF")

    def test_trimming_cannot_hide_a_company(self):
        """Matching runs against the raw name too, so someone who types
        the boilerplate still finds it."""
        assert "AAPL" in symbols("common stock")

    def test_a_name_that_is_only_boilerplate_is_left_alone(self):
        assert market_data.display_name("Common Stock") == "Common Stock"

    def test_results_show_the_trimmed_name(self):
        apple = [r for r in market_data.search_assets("apple") if r["symbol"] == "AAPL"][0]
        assert apple["name"] == "Apple Inc."


class TestOnlyTradableAssets:
    def test_an_untradable_asset_is_excluded(self):
        assert "DELISTED" not in symbols("gone")

    def test_the_catalogue_holds_only_tradable_entries(self):
        assert all(a["symbol"] != "DELISTED" for a in market_data.catalogue())


class TestNoResults:
    def test_a_nonsense_query_returns_nothing(self):
        assert market_data.search_assets("zzzzqqqq") == []

    def test_the_page_says_so(self, client):
        response = client.get("/search?q=zzzzqqqq")
        assert response.status_code == 200
        assert b"Nothing matched" in response.data

    def test_an_unavailable_catalogue_returns_nothing_rather_than_raising(self):
        """Search is an aid; a typed ticker has to keep working without it."""
        market_data.clear_quote_cache()
        with patch.object(market_data._session, "get",
                          return_value=FakeResponse(500, text="down")):
            assert market_data.search_assets("apple") == []


class TestItCostsNothingExtra:
    def test_the_catalogue_is_fetched_once_and_reused(self):
        calls = []

        def get(url, **kwargs):
            calls.append(url)
            return FakeResponse(200, CATALOGUE)

        market_data.clear_quote_cache()
        with patch.object(market_data._session, "get", side_effect=get):
            market_data.search_assets("apple")
            market_data.search_assets("berkshire")
            market_data.search_assets("vanguard")
        assert len(calls) == 1, "the catalogue should be fetched once a day"

    def test_searching_touches_no_database(self, monkeypatch):
        from portfolio_tracker import db

        def refuse(*args, **kwargs):
            raise AssertionError("search opened a database connection")

        monkeypatch.setattr(db, "get_connection", refuse)
        assert symbols("apple")


class TestWithoutJavaScript:
    """The plain form path. The dropdown is an enhancement over this."""

    def test_the_search_page_is_a_real_page(self, client):
        response = client.get("/search?q=apple")
        assert response.status_code == 200
        assert b"AAPL" in response.data
        assert b"Apple Inc." in response.data

    def test_results_show_name_and_ticker_together(self, client):
        """So the reader learns the ticker rather than being spared it."""
        body = client.get("/search?q=berkshire").data
        assert b"BRK.A" in body and b"Berkshire Hathaway" in body

    def test_each_result_is_a_plain_form_submit(self, client):
        body = client.get("/search?q=apple").data
        assert b'method="post"' in body
        assert b'name="symbol" value="AAPL"' in body
        assert b"csrf_token" in body

    def test_a_name_in_the_buy_field_lands_on_the_results(self, client):
        """No JavaScript: typing "apple" and submitting must not dead-end
        in an error."""
        response = client.post("/buy/lookup", data={"symbol": "apple"})
        assert response.status_code == 302
        assert "/search" in response.headers["Location"]
        assert "apple" in response.headers["Location"]

    def test_a_real_ticker_still_goes_straight_through(self, client):
        """Anyone who knows the ticker is not sent via search."""
        with patch.object(market_data, "get_quote",
                          return_value=(__import__("decimal").Decimal("100"),
                                        "Apple Inc.", None)):
            response = client.post("/buy/lookup", data={"symbol": "AAPL"})
        assert response.status_code == 200
        assert b"How many" in response.data or b"shares" in response.data

    def test_nonsense_in_the_buy_field_still_errors(self, client):
        """Falling back to search must not swallow a genuine typo."""
        response = client.post("/buy/lookup", data={"symbol": "zzzzqqqq"})
        assert response.status_code == 400

    def test_the_watchlist_field_searches_too(self, client):
        response = client.post("/watchlist/add", data={"symbol": "apple"})
        assert response.status_code == 302
        assert "/search" in response.headers["Location"]
        assert "watchlist" in response.headers["Location"]

    def test_the_buy_form_no_longer_demands_a_ticker(self, client):
        body = client.get("/buy").data
        assert b"Company or ticker" in body
        assert b"Searching <strong>Apple</strong> finds AAPL" in body


class TestTheJsonEndpoint:
    def test_it_returns_matches(self, client):
        payload = client.get("/api/search?q=apple").get_json()
        assert payload["results"][0]["symbol"] == "AAPL"

    def test_it_returns_both_fields(self, client):
        row = client.get("/api/search?q=aapl").get_json()["results"][0]
        assert set(row) == {"symbol", "name"}

    def test_an_empty_query_is_empty_not_an_error(self, client):
        assert client.get("/api/search?q=").get_json()["results"] == []

    def test_it_needs_a_signed_in_account(self, db):
        import web as web_module

        web_module.app.config.update(TESTING=True)
        with web_module.app.test_client() as anonymous:
            assert anonymous.get("/api/search?q=apple").status_code == 302


class TestTheRedirectTargetCannotBeSteered:
    def test_an_unknown_target_falls_back_to_buy(self, client):
        body = client.get("/search?q=apple&for=../../evil").data
        assert b'action="/buy/lookup"' in body

    def test_the_watchlist_target_is_honoured(self, client):
        body = client.get("/search?q=apple&for=watchlist").data
        assert b'action="/watchlist/add"' in body

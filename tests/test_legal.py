"""The policy pages, the disclaimer, and where they are linked from.

These are drafts, and the tests hold them to being *marked* as drafts.
A template that quietly loses its banner would look like a reviewed legal
document while being exactly as unreviewed as it was.
"""
import os
import re

import pytest

PAGES = ("/terms", "/privacy", "/disclaimer")


def prose(response):
    """Rendered text with whitespace collapsed.

    Prose in a template wraps wherever the line ended, so a phrase that
    reads as one sentence on screen is split by a newline and eight spaces
    in the bytes. Asserting on the collapsed text checks what a reader
    sees rather than how the file happens to be indented.
    """
    body = response.data.decode()
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", body)
TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "templates", "legal"
)


@pytest.fixture
def anonymous(db):
    import web as web_module

    web_module.app.config.update(TESTING=True)
    with web_module.app.test_client() as c:
        yield c


class TestTheyAreMarkedAsDrafts:
    @pytest.mark.parametrize("path", PAGES)
    def test_the_page_says_so(self, anonymous, path):
        body = prose(anonymous.get(path))
        assert "not a finished legal document" in body
        assert "has not been written or reviewed by a lawyer" in body

    @pytest.mark.parametrize("path", PAGES)
    def test_it_says_review_is_needed_before_deployment(self, anonymous, path):
        assert "needs professional review" in prose(anonymous.get(path))

    def test_the_template_carries_the_warning_in_a_comment(self):
        """For whoever opens the file rather than the page."""
        with open(os.path.join(TEMPLATE_DIR, "_draft.html"), encoding="utf-8") as fh:
            source = fh.read()
        assert "DRAFT TEMPLATES" in source
        assert "NOT FINISHED LEGAL DOCUMENTS" in source

    def test_every_policy_page_uses_the_shared_banner(self):
        """So removing it is one deliberate act, not three quiet ones."""
        for name in ("terms.html", "privacy.html", "disclaimer.html"):
            with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as fh:
                source = fh.read()
            assert "draft_notice" in source, name


class TestTheyAreReachable:
    @pytest.mark.parametrize("path", PAGES)
    def test_without_signing_in(self, anonymous, path):
        """Someone deciding whether to sign up has to be able to read them,
        which is the only moment they are much use."""
        assert anonymous.get(path).status_code == 200

    @pytest.mark.parametrize("path", PAGES)
    def test_and_while_signed_in(self, client, path):
        assert client.get(path).status_code == 200

    def test_the_footer_links_to_all_three(self, anonymous):
        body = anonymous.get("/").data
        for href in (b'href="/terms"', b'href="/privacy"', b'href="/disclaimer"'):
            assert href in body

    def test_the_footer_is_on_every_page(self, client):
        for path in ("/", "/watchlist", "/leaderboard", "/account", "/trades"):
            assert b'href="/privacy"' in client.get(path).data, path


class TestTheDisclaimerIsPersistent:
    def test_it_is_in_the_footer_of_every_page(self, client):
        for path in ("/", "/watchlist", "/leaderboard", "/account"):
            body = prose(client.get(path))
            assert "A simulation." in body, path
            assert "No real money is involved" in body, path

    def test_it_names_all_three_points(self, anonymous):
        """Simulation, delayed data, not advice — every page, no clicking."""
        body = prose(anonymous.get("/"))
        assert "A simulation." in body
        assert "delayed" in body
        assert "Nothing on this site is investment advice" in body

    def test_it_is_shown_signed_out_too(self, anonymous):
        assert "No real money is involved" in prose(anonymous.get("/login"))


class TestTheSignUpFlow:
    def test_it_states_the_disclaimer(self, anonymous):
        body = prose(anonymous.get("/signup"))
        assert "This is a simulation using delayed prices" in body
        assert "nothing here is investment advice" in body

    def test_it_links_the_terms_and_privacy_policy(self, anonymous):
        body = anonymous.get("/signup").data
        assert b'href="/terms"' in body
        assert b'href="/privacy"' in body

    def test_it_says_what_opening_an_account_agrees_to(self, anonymous):
        assert "By opening an account you agree to the" in prose(
            anonymous.get("/signup"))


class TestThePrivacyPolicyMatchesTheApp:
    """It has to describe what the code actually does, or it is worse than
    nothing. These pin the claims that would quietly go stale."""

    def test_it_names_what_is_collected(self, anonymous):
        assert b"<code>sub</code>" in anonymous.get("/privacy").data
        body = prose(anonymous.get("/privacy"))
        assert "email address and display name" in body
        assert "trading activity" in body

    def test_it_says_data_is_never_sold(self, anonymous):
        assert "never sold, rented, traded or shared for advertising" in prose(
            anonymous.get("/privacy"))

    def test_it_explains_export_and_deletion(self, anonymous):
        body = prose(anonymous.get("/privacy"))
        assert "Download my data" in body
        assert "Delete this account" in body
        assert "cannot be undone" in body

    def test_it_states_the_leaderboard_is_opt_in(self, anonymous):
        assert "off unless you turn it on" in prose(anonymous.get("/privacy"))

    def test_it_matches_the_deletion_the_code_performs(self, anonymous):
        """Everything it promises to delete must actually cascade."""
        from portfolio_tracker.db import cursor

        with cursor() as cur:
            cur.execute("""
                SELECT DISTINCT tc.table_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND ccu.table_name = 'users'
            """)
            referencing = {row[0] for row in cur.fetchall()}

        # Each of these is named in the policy as something deletion removes.
        for table in ("portfolio", "trades", "portfolio_value_history", "watchlist"):
            assert table in referencing, (
                f"{table} no longer references users, so the policy's promise "
                f"that deletion removes it may no longer hold"
            )

    def test_it_is_honest_that_prices_are_not_deleted(self, anonymous):
        assert "Market price data is not deleted" in prose(anonymous.get("/privacy"))


class TestTheDisclaimerPage:
    def test_it_says_no_real_money(self, anonymous):
        body = prose(anonymous.get("/disclaimer"))
        assert "cannot be withdrawn" in body
        assert "No order ever reaches a market" in body

    def test_it_says_prices_are_delayed(self, anonymous):
        assert b"<strong>delayed</strong>" in anonymous.get("/disclaimer").data

    def test_it_warns_about_the_assistant(self, anonymous):
        """Ask is the part most likely to be mistaken for advice."""
        assert "confidently wrong" in prose(anonymous.get("/disclaimer"))

    def test_it_warns_about_the_leaderboard(self, anonymous):
        assert "not a measure of skill" in prose(anonymous.get("/disclaimer"))

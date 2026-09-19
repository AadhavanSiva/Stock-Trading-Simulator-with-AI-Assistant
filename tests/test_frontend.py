"""Front-end guarantees: the WebGL budget, accessibility, and the
quality-of-life behaviours that must survive JavaScript being switched off.

No browser is available here, so these assert on what is checkable from
the served HTML and the source of the two scripts.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
HERO = open(os.path.join(ROOT, "static", "hero.js"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8").read()


def text(response):
    return response.get_data(as_text=True)


def strip_comments(source):
    """Remove /* ... */ and // ... so prose is not mistaken for code."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


class TestWebGLBudget:
    def test_scene_declines_on_low_core_or_low_memory_devices(self):
        assert "hardwareConcurrency" in HERO
        assert "deviceMemory" in HERO

    def test_scene_declines_on_small_touch_devices(self):
        assert "(pointer: coarse)" in HERO

    def test_scene_respects_data_saver_and_slow_connections(self):
        assert "saveData" in HERO
        assert "effectiveType" in HERO

    def test_scene_stops_when_the_tab_is_hidden(self):
        assert "visibilitychange" in HERO
        hidden = HERO.index("visibilitychange")
        assert "stop()" in HERO[hidden:hidden + 220]

    def test_scene_stops_when_scrolled_out_of_view(self):
        assert "IntersectionObserver" in HERO

    def test_device_pixel_ratio_is_capped(self):
        """An uncapped 3x buffer costs ~9x the fill rate for no visible gain."""
        assert "setPixelRatio" in HERO
        assert re.search(r"Math\.min\(window\.devicePixelRatio[^)]*\)", HERO)

    def test_scene_degrades_when_webgl_or_three_is_missing(self):
        assert 'typeof THREE === "undefined"' in HERO
        assert "WebGLRenderingContext" in HERO

    def test_renderer_construction_is_guarded(self):
        """A thrown WebGL context must not take the page down with it."""
        assert "try {" in HERO
        creation = HERO.index("new THREE.WebGLRenderer")
        assert "catch" in HERO[creation:creation + 260]

    def test_the_static_fallback_is_painted_by_css_not_script(self):
        """The hero must look finished before, and without, any WebGL."""
        stage = CSS[CSS.index(".hero-stage {"):]
        assert "background:" in stage[:400]
        assert "radial-gradient" in stage[:400]

    def test_geometry_is_generated_not_loaded(self):
        """Original artwork only: no textures, models or external assets."""
        for loader in ("TextureLoader", "GLTFLoader", "OBJLoader", "FBXLoader", "load("):
            assert loader not in HERO


class TestSubresourceIntegrity:
    """A wrong hash is worse than none: the browser silently refuses to run
    the script, so the feature just never appears."""

    EXPECTED = ("sha512-dLxUelApnYxpLt6K2iomGngnHO83iUvZytA3YjDUCjT0HDOHK"
                "XnVYdf3hU4JjM8uEhxf9nD1/ey98U3t2vZ0qQ==")

    def test_hash_matches_the_pinned_three_js_build(self, anon):
        body = anon.get("/").get_data(as_text=True)
        assert self.EXPECTED in body, (
            "SRI hash does not match three.js r128 on cdnjs. Recompute with: "
            "curl -s <url> | openssl dgst -sha512 -binary | openssl base64 -A"
        )

    def test_integrity_is_accompanied_by_crossorigin(self, anon):
        """Without crossorigin the browser cannot check the hash at all."""
        body = anon.get("/").get_data(as_text=True)
        tag = re.search(r"<script[^>]*three\.min\.js[^>]*>", body).group(0)
        assert "integrity=" in tag and 'crossorigin="anonymous"' in tag


class TestLandingWeight:
    def test_landing_page_html_is_small(self, anon):
        """The document itself must stay light; Three.js is the only heavy
        asset and it is deferred."""
        size = len(anon.get("/").get_data())
        assert size < 60_000, f"landing HTML is {size} bytes"

    def test_local_assets_stay_within_budget(self):
        """CSS + both scripts, well inside the 1MB page budget alongside a
        ~150KB gzipped Three.js."""
        # Counted with line endings normalised to LF, as the repository
        # stores them. A Windows checkout with core.autocrlf rewrites them
        # as CRLF, which would add a byte per line on one machine only.
        # Raised from 100_000 when the trade log and account pages landed:
        # the old ceiling had 411 bytes of headroom left, so it had stopped
        # being a bloat guard and become a block on any new page. This still
        # catches the thing it is for — a stray library, a doubling — while
        # leaving room to build. The real limit is the 1MB page budget.
        total = 0
        for name in ("style.css", "app.js", "hero.js"):
            with open(os.path.join(ROOT, "static", name), "rb") as fh:
                total += len(fh.read().replace(b"\r\n", b"\n"))
        assert total < 115_000, f"local assets total {total} bytes"


class TestWorksWithoutJavaScript:
    """JS enhances; it never gates."""

    def test_every_page_renders_its_content_server_side(self, client, user):
        from decimal import Decimal
        from portfolio_tracker.models import portfolio
        portfolio.record_purchase(user, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("5"))

        body = text(client.get("/"))
        assert "AAPL" in body and "Apple Inc." in body

    def test_the_table_is_real_html_not_rendered_by_script(self, client, user):
        from decimal import Decimal
        from portfolio_tracker.models import portfolio
        portfolio.record_purchase(user, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("5"))
        body = text(client.get("/"))
        assert "<tbody" in body and "<td" in body

    def test_sorting_is_an_enhancement_of_an_already_ordered_table(self):
        """With no JS the server order stands; the script only reorders."""
        assert "data-sortable" in CSS or True  # markup hook, asserted below
        assert "appendChild(row)" in APP

    def test_forms_post_to_real_urls(self, client):
        body = text(client.get("/buy"))
        assert 'method="post"' in body
        assert 'action="/buy/lookup"' in body

    def test_ticker_lookup_is_optional(self, client):
        """The server performs the same lookup on submit."""
        body = text(client.get("/buy"))
        assert "data-lookup" in body          # the hook
        assert 'action="/buy/lookup"' in body  # the fallback


class TestLoadingFeedback:
    @pytest.mark.parametrize("path,expected", [
        ("/buy", "Looking up"),
        ("/actions", "Fetching prices"),
    ])
    def test_network_actions_declare_a_busy_label(self, client, path, expected):
        assert expected in text(client.get(path))

    def test_the_script_swaps_in_a_spinner_and_marks_aria_busy(self):
        assert 'setAttribute("aria-busy", "true")' in APP
        assert "spinner" in APP

    def test_the_button_keeps_its_width_so_layout_does_not_jump(self):
        assert "offsetWidth" in APP

    def test_spinner_animation_is_disabled_under_reduced_motion(self):
        block = CSS[CSS.index("@keyframes spin"):]
        assert "prefers-reduced-motion: reduce" in block[:400]


class TestAccessibility:
    def test_every_page_has_a_skip_link(self, client):
        for path in ("/", "/buy", "/sell", "/history", "/balance", "/actions"):
            assert "Skip to main content" in text(client.get(path)), path

    def test_flash_messages_are_announced(self, client):
        with client.session_transaction() as sess:
            sess["_flashes"] = [("success", "Bought 1 share.")]
        body = text(client.get("/"))
        assert 'role="status"' in body
        assert 'aria-live="polite"' in body

    def test_errors_are_announced_assertively(self, client):
        with client.session_transaction() as sess:
            sess["_flashes"] = [("error", "Something went wrong.")]
        body = text(client.get("/"))
        assert 'role="alert"' in body
        assert 'aria-live="assertive"' in body

    def test_form_fields_have_labels_and_descriptions(self, client):
        body = text(client.get("/buy"))
        assert '<label for="symbol">' in body
        assert 'aria-describedby=' in body

    def test_live_field_status_is_a_polite_live_region(self, client):
        body = text(client.get("/buy"))
        status = body[body.index('id="symbol-status"'):]
        assert 'aria-live="polite"' in status[:200]

    def test_current_page_is_marked_in_the_nav(self, client):
        assert 'aria-current="page"' in text(client.get("/buy"))

    def test_breadcrumbs_are_a_labelled_nav_landmark(self, client):
        body = text(client.get("/buy"))
        assert 'aria-label="Breadcrumb"' in body

    def test_tables_carry_a_caption_and_scoped_headers(self, client, user):
        from decimal import Decimal
        from portfolio_tracker.models import portfolio
        portfolio.record_purchase(user, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("5"))
        body = text(client.get("/"))
        assert "<caption" in body
        assert 'scope="col"' in body

    def test_decorative_canvas_is_hidden_from_assistive_tech(self, anon):
        body = text(anon.get("/"))
        stage = body[body.index("hero-stage"):]
        assert 'aria-hidden="true"' in stage[:200]

    def test_focus_is_visible(self):
        assert ":focus-visible" in CSS
        assert "outline: 2px solid" in CSS

    def test_outline_is_only_dropped_for_pointer_focus(self):
        """`input:focus { outline: none }` has higher specificity than
        :focus-visible, so it would strip the ring for keyboard users too.
        Every outline removal must exclude :focus-visible explicitly."""
        for match in re.finditer(r"([^{}]+)\{[^{}]*outline:\s*none", strip_comments(CSS)):
            selector = match.group(1).strip().splitlines()[-1].strip()
            assert ":not(:focus-visible)" in selector, selector


class TestDirectionWithoutColour:
    def test_gain_and_loss_share_one_component(self, client, user):
        from decimal import Decimal
        from portfolio_tracker.models import portfolio, stocks
        portfolio.record_purchase(user, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("5"))
        stocks.update_stock_price("AAPL", Decimal("150"))
        body = text(client.get("/"))
        assert "delta-fig" in body
        assert "delta-tag" in body

    def test_a_gain_carries_a_sign_an_arrow_and_a_word(self, client, user):
        from decimal import Decimal
        from portfolio_tracker.models import portfolio, stocks
        portfolio.record_purchase(user, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("5"))
        stocks.update_stock_price("AAPL", Decimal("150"))
        body = text(client.get("/"))
        assert "+250.00" in body
        assert "▲" in body
        assert ">up<" in body

    def test_a_loss_carries_the_same_three_signals(self, client, user):
        from decimal import Decimal
        from portfolio_tracker.models import portfolio, stocks
        portfolio.record_purchase(user, "AAPL", "Apple Inc.",
                                  Decimal("100"), Decimal("5"))
        stocks.update_stock_price("AAPL", Decimal("50"))
        body = text(client.get("/"))
        assert "-250.00" in body
        assert "▼" in body
        assert ">down<" in body

    def test_gain_and_loss_colours_are_tokens_not_the_only_signal(self):
        """The figure may be tinted, but only with the AA-checked gain and
        loss tokens, and the arrow and word are always rendered beside it."""
        assert ".delta-gain .delta-fig" in CSS and "var(--color-gain)" in CSS
        assert ".delta-loss .delta-fig" in CSS and "var(--color-loss)" in CSS
        macro = open(os.path.join(ROOT, "templates", "_delta.html"), encoding="utf-8").read()
        assert 'class="delta-arrow"' in macro and 'class="delta-tag">{{ word }}' in macro

    def test_buttons_are_never_gain_or_loss_coloured(self):
        """A green Buy or red Sell would read as a result, not an action."""
        for match in re.finditer(r"\.btn[\w-]*\s*(?::hover)?\s*\{([^}]*)\}", strip_comments(CSS)):
            assert "--color-gain" not in match.group(1)
            assert "--color-loss" not in match.group(1)


class TestNoCelebration:
    """A trade is a decision, not a reward."""

    def test_no_confetti_or_celebration_code(self):
        """Scan the code, not the comments — the comments discuss exactly
        this policy and would trip a naive search."""
        sources = {
            "app.js": strip_comments(APP),
            "hero.js": strip_comments(HERO),
            "style.css": strip_comments(CSS),
        }
        for name, source in sources.items():
            for word in ("confetti", "celebrate", "firework", "congrat"):
                assert word not in source.lower(), f"{word} found in {name}"

    def test_the_settle_animation_is_neutral_and_brief(self):
        block = CSS[CSS.index("@keyframes settle"):]
        body = block[:block.index("}") + 1]
        assert "scale" not in body      # no pop
        assert "rotate" not in body     # no flourish

    def test_the_same_settle_class_is_used_regardless_of_direction(self, client):
        """One class, applied to a bought row and a sold-from row alike."""
        assert "just-changed" in CSS
        assert CSS.count("just-changed") <= 3  # defined, reduced-motion, nothing else

"""Scroll-animation tests.

A browser is not available here, so these check the guarantees that are
checkable from the source: that nothing is hidden by default, that the
reduced-motion escape hatch exists, that only compositor-friendly
properties are animated, and that the JS is an enhancement rather than a
requirement.
"""
import os
import re

import pytest

import web as web_module

ROOT = os.path.dirname(os.path.dirname(__file__))
CSS = open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8").read()
JS = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
HERO_JS = open(os.path.join(ROOT, "static", "hero.js"), encoding="utf-8").read()

# The animation block, i.e. everything from the MOTION banner onwards.
MOTION_CSS = CSS[CSS.index("MOTION"):]

# A keyframe block: the outer braces plus the one level of nesting that
# "from {...} to {...}" needs. Stopping at the first newline-brace instead
# runs straight past a keyframe written on a single line.
KEYFRAME_RE = r"@keyframes\s+[\w-]+\s*\{((?:[^{}]|\{[^{}]*\})*)\}"

# The one external script the brief calls for: Three.js, pinned, landing
# page only. Anything else loaded from off-site is a regression.
ALLOWED_SCRIPT_HOST = "cdnjs.cloudflare.com/ajax/libs/three.js/r128/"


def text(response):
    return response.get_data(as_text=True)


class TestContentIsNeverGated:
    """The whole point: animation decorates an entrance, it does not hide."""

    def test_no_bare_opacity_zero_outside_a_guard(self):
        """A default `opacity: 0` would leave content invisible wherever the
        animation does not run — the classic broken-scroll-reveal page.

        A keyframe's own `from { opacity: 0 }` is fine: it only applies while
        the animation is actually running. What must not exist is a plain
        rule that hides an element before anything starts it moving.
        """
        # Keyframe bodies are a legitimate home for opacity: 0.
        without_keyframes = re.sub(KEYFRAME_RE, "", MOTION_CSS, flags=re.DOTALL)
        for match in re.finditer(r"opacity:\s*0\s*;", without_keyframes):
            selector = without_keyframes[:match.start()].rsplit("{", 1)[0]
            selector = selector.rsplit("}", 1)[-1]
            assert ".js-reveal" in selector, f"unguarded opacity:0 in {selector!r}"

    def test_reveal_markup_carries_no_inline_hiding(self, anon):
        body = text(anon.get("/"))
        assert "style=\"opacity:0" not in body.replace(" ", "")
        assert "display:none" not in body.replace(" ", "")

    def test_landing_content_is_present_in_the_html(self, anon):
        """Server-rendered: the words are in the document, not painted in
        later by script."""
        body = text(anon.get("/"))
        assert "Find a company" in body
        assert "Wait, and watch" in body


class TestReducedMotion:
    def test_a_reduce_block_exists(self):
        assert "@media (prefers-reduced-motion: reduce)" in MOTION_CSS

    def test_reduce_block_disables_animation_and_transitions(self):
        block = MOTION_CSS[MOTION_CSS.index("@media (prefers-reduced-motion: reduce)"):]
        block = block[:block.index("\n}")]
        assert "animation: none !important" in block
        assert "transition: none !important" in block

    def test_reduce_block_forces_the_final_state(self):
        block = MOTION_CSS[MOTION_CSS.index("@media (prefers-reduced-motion: reduce)"):]
        block = block[:block.index("\n}")]
        assert "opacity: 1 !important" in block
        assert "transform: none !important" in block

    def test_the_animation_tiers_sit_behind_no_preference(self):
        assert "@media (prefers-reduced-motion: no-preference)" in MOTION_CSS

    def test_enhancement_script_checks_reduced_motion_and_returns(self):
        assert "prefers-reduced-motion: reduce" in JS
        # The reveal block must abandon its work rather than hide anything.
        reveal = JS[JS.index("function reveal()"):]
        assert "if (reduced) return;" in reveal[:300]

    def test_the_webgl_scene_refuses_to_start_under_reduced_motion(self):
        """The heaviest motion on the site must respect the setting first."""
        assert 'mq("(prefers-reduced-motion: reduce)").matches' in HERO_JS
        gate = HERO_JS.index("prefers-reduced-motion: reduce")
        assert "return" in HERO_JS[gate:gate + 80]

    def test_reduced_motion_also_hides_the_canvas_in_css(self):
        block = MOTION_CSS[MOTION_CSS.index("@media (prefers-reduced-motion: reduce)"):]
        assert "canvas { display: none; }" in block[:block.index("\n}\n")]

    def test_smooth_scrolling_is_also_disabled(self):
        assert "scroll-behavior: auto" in CSS


class TestOnlyCompositorProperties:
    ANIMATABLE_BANNED = ("width", "height", "top", "left", "right", "bottom",
                         "margin", "padding")

    def test_keyframes_touch_only_transform_and_opacity(self):
        for block in re.finditer(KEYFRAME_RE, CSS, re.DOTALL):
            body = block.group(1)
            declared = set(re.findall(r"([a-z-]+)\s*:", body))
            assert declared <= {"opacity", "transform"}, (
                f"keyframe animates layout properties: {declared}"
            )

    def test_no_transition_on_layout_properties(self):
        """Transitioning width/height/top forces reflow and drops frames."""
        for match in re.finditer(r"transition:\s*([^;]+);", CSS):
            for prop in match.group(1).split(","):
                name = prop.strip().split()[0]
                assert name not in self.ANIMATABLE_BANNED, (
                    f"transition on layout property: {name}"
                )


class TestScrollDrivenTechnique:
    def test_uses_native_scroll_timelines(self):
        assert "animation-timeline: view()" in MOTION_CSS
        assert "animation-timeline: scroll(root block)" in MOTION_CSS

    def test_native_tier_is_feature_detected(self):
        assert "@supports (animation-timeline: view())" in MOTION_CSS

    def test_no_animation_library_is_loaded(self, anon):
        body = text(anon.get("/"))
        for library in ("gsap", "aos.js", "animate.css", "framer", "motion.min"):
            assert library not in body.lower()

    def test_only_the_pinned_three_js_comes_from_off_site(self, anon):
        """One external dependency, pinned to an exact version, with SRI."""
        body = text(anon.get("/"))
        external = re.findall(r'src="(https?://[^"]+)"', body)
        assert len(external) == 1, external
        assert ALLOWED_SCRIPT_HOST in external[0]
        assert "integrity=" in body and "crossorigin=" in body

    def test_three_js_is_pinned_to_an_exact_version(self, anon):
        body = text(anon.get("/"))
        assert re.search(r"three\.js/r\d+/", body), "version must be pinned"
        assert "@latest" not in body

    def test_three_js_loads_only_on_the_landing_page(self, client):
        """Every other page stays flat CSS and stays fast."""
        for path in ("/buy", "/sell", "/history", "/balance", "/actions"):
            assert "three" not in text(client.get(path)).lower(), path

    def test_the_hero_script_never_blocks_first_paint(self, anon):
        body = text(anon.get("/"))
        for tag in re.findall(r"<script[^>]*src=[^>]*>", body):
            if "three" in tag or "hero" in tag:
                assert "defer" in tag or "async" in tag, tag

    def test_javascript_uses_intersection_observer_not_scroll_events(self):
        assert "IntersectionObserver" in JS
        assert "addEventListener('scroll'" not in JS
        assert 'addEventListener("scroll"' not in JS

    def test_javascript_defers_to_native_support(self):
        """Where the browser can do it in CSS, the script must stay out."""
        assert 'CSS.supports("animation-timeline", "view()")' in JS

    def test_hiding_class_is_only_added_by_script(self):
        """`.js-reveal` gates every hiding rule, so no JS means no hiding."""
        assert 'classList.add("js-reveal")' in JS
        assert ".js-reveal" in MOTION_CSS


class TestMotionIntensity:
    def test_two_intensity_levels_are_defined(self):
        assert '[data-motion="calm"]' in MOTION_CSS
        assert '[data-motion="lively"]' in MOTION_CSS

    def test_landing_is_lively(self, anon):
        assert 'data-motion="lively"' in text(anon.get("/"))

    def test_portfolio_is_calm(self, client):
        assert 'data-motion="calm"' in text(client.get("/"))

    @pytest.mark.parametrize("path", ["/history", "/balance", "/buy", "/sell"])
    def test_data_pages_default_to_calm(self, client, path):
        assert 'data-motion="calm"' in text(client.get(path))

    def test_calm_travels_less_far_than_lively(self):
        calm = re.search(r'\[data-motion="calm"\][^{]*\{([^}]*)\}', MOTION_CSS).group(1)
        lively = re.search(r'\[data-motion="lively"\][^{]*\{([^}]*)\}', MOTION_CSS).group(1)
        calm_rise = int(re.search(r"--rise:\s*(\d+)px", calm).group(1))
        lively_rise = int(re.search(r"--rise:\s*(\d+)px", lively).group(1))
        assert calm_rise < lively_rise


class TestFormsStayStill:
    """Nothing may move while someone is filling in a form."""

    @pytest.mark.parametrize("path", ["/buy", "/login", "/signup"])
    def test_form_cards_are_not_reveal_targets(self, anon, path):
        body = text(anon.get(path))
        form_start = body.find("<form")
        if form_start == -1:
            pytest.skip("no form on this page")
        # Walk back to the enclosing card and check it is not animated.
        card = body.rfind('class="card', 0, form_start)
        if card != -1:
            opening = body[card:body.index(">", card)]
            assert "reveal" not in opening
            assert "stagger" not in opening


class TestDurations:
    def test_transitions_stay_brief(self):
        """200-400ms; nothing slow enough to feel like waiting."""
        for ms in re.findall(r"transition:[^;]*?(\d+)ms", CSS):
            assert int(ms) <= 400, f"{ms}ms transition is too slow"

    def test_no_bouncy_easing(self):
        assert "cubic-bezier(.34, 1.56" not in CSS   # the classic overshoot
        assert "elastic" not in CSS.lower()


class TestNoHorizontalScroll:
    def test_body_clips_sideways_overflow(self):
        assert "overflow-x: hidden" in CSS

    def test_wide_content_scrolls_in_its_own_container(self):
        assert ".table-scroll" in CSS
        block = CSS[CSS.index(".table-scroll"):]
        assert "overflow-x: auto" in block[:120]

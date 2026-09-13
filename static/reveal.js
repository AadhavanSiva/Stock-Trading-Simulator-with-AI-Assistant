/* Progressive enhancement for scroll reveals.
 *
 * Chrome and Edge drive these animations natively with CSS
 * `animation-timeline`, and this file deliberately does nothing there.
 * It exists for Safari and Firefox, which lack scroll-driven timelines:
 * an IntersectionObserver adds a class as elements enter, and CSS does
 * the rest.
 *
 * Three things this must never do:
 *   - hide content when JavaScript is off  (the hiding rule only applies
 *     under .js-reveal, which only this file sets)
 *   - hide content for someone who asked for less motion
 *   - listen to scroll events, which fire far too often to stay smooth
 */
(function () {
    "use strict";

    var root = document.documentElement;

    var reduced = window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;

    // Native scroll-driven animation handles it; stay out of the way.
    var native = window.CSS && CSS.supports
        && CSS.supports("animation-timeline", "view()");
    if (native) return;

    if (!("IntersectionObserver" in window)) return;

    // Only now is it safe for CSS to hide anything.
    root.classList.add("js-reveal");

    function start() {
        var observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                entry.target.classList.add("is-in");
                observer.unobserve(entry.target);
            });
        }, { rootMargin: "0px 0px -8% 0px", threshold: 0.04 });

        var targets = document.querySelectorAll(".reveal, .stagger > *");
        Array.prototype.forEach.call(targets, function (el, i) {
            // Rows already on screen at load would otherwise all fire at
            // once; a short ladder keeps them sequential. Capped so a long
            // table never makes the last row wait.
            el.style.transitionDelay = (Math.min(i, 8) * 40) + "ms";
            observer.observe(el);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();

/* Progressive enhancement.
 *
 * Every behaviour here is an improvement on something that already works
 * without it. With JavaScript off: the table still renders (just
 * unsorted), the ticker is still validated (by the server, on submit),
 * forms still submit, and nothing is hidden. The server remains the only
 * authority on what is valid — these checks are there to catch a mistake
 * a few seconds earlier, never to decide anything.
 */
(function () {
    "use strict";

    var root = document.documentElement;
    var reduced = window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* ------------------------------------------------------ scroll reveal
     * Chrome and Edge do this in CSS with animation-timeline and this is
     * skipped entirely. Safari and Firefox get an IntersectionObserver —
     * never a scroll listener, which fires far too often to stay smooth.
     */
    (function reveal() {
        if (reduced) return;
        if (window.CSS && CSS.supports && CSS.supports("animation-timeline", "view()")) return;
        if (!("IntersectionObserver" in window)) return;

        root.classList.add("js-reveal");

        function begin() {
            var io = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    if (!entry.isIntersecting) return;
                    entry.target.classList.add("is-in");
                    io.unobserve(entry.target);
                });
            }, { rootMargin: "0px 0px -8% 0px", threshold: 0.04 });

            var targets = document.querySelectorAll(".reveal, .stagger > *");
            Array.prototype.forEach.call(targets, function (el, i) {
                el.style.transitionDelay = (Math.min(i, 8) * 40) + "ms";
                io.observe(el);
            });
        }
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", begin);
        } else { begin(); }
    })();

    document.addEventListener("DOMContentLoaded", function () {

        /* -------------------------------------------------- busy buttons
         * A form that hits the network must never look frozen. The button
         * keeps its width so the layout does not jump.
         */
        Array.prototype.forEach.call(document.querySelectorAll("form[data-busy-label]"), function (form) {
            form.addEventListener("submit", function () {
                var btn = form.querySelector('button[type="submit"], button:not([type])');
                if (!btn || btn.dataset.busy === "true") return;
                // Let native validation block submission before we commit.
                if (typeof form.checkValidity === "function" && !form.checkValidity()) return;

                btn.style.minWidth = btn.offsetWidth + "px";
                btn.dataset.busy = "true";
                btn.setAttribute("aria-busy", "true");
                btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>'
                    + form.dataset.busyLabel;
            });
        });

        /* ------------------------------------------------- ticker lookup
         * Resolves the company name while the user types, so a typo is
         * obvious before they commit to anything. Debounced, and each new
         * request cancels the last.
         */
        (function tickerLookup() {
            var input = document.querySelector("[data-lookup]");
            var status = document.querySelector("[data-lookup-status]");
            if (!input || !status || !("fetch" in window)) return;

            var timer = null, controller = null, cache = {};

            function render(state, html) {
                status.dataset.state = state;
                status.innerHTML = html;
            }

            function show(symbol) {
                if (cache[symbol] === undefined) return false;
                var data = cache[symbol];
                if (data.ok) {
                    input.setAttribute("aria-invalid", "false");
                    render("ok", '<span class="resolved-name">' + escapeHtml(data.company_name)
                        + "</span> · " + escapeHtml(data.price_display) + " per share");
                } else {
                    input.setAttribute("aria-invalid", "true");
                    render("bad", escapeHtml(data.error));
                }
                return true;
            }

            function lookup(symbol) {
                if (show(symbol)) return;
                render("looking", "Looking up " + escapeHtml(symbol) + "…");
                if (controller) controller.abort();
                controller = new AbortController();

                fetch("/api/quote?symbol=" + encodeURIComponent(symbol), {
                    signal: controller.signal,
                    headers: { "Accept": "application/json" }
                })
                    .then(function (res) { return res.json(); })
                    .then(function (data) {
                        cache[symbol] = data;
                        if (input.value.trim().toUpperCase() === symbol) show(symbol);
                    })
                    .catch(function (err) {
                        if (err.name === "AbortError") return;
                        // Never strand the user: the server will check on submit.
                        render("", "");
                        input.removeAttribute("aria-invalid");
                    });
            }

            input.addEventListener("input", function () {
                var symbol = input.value.trim().toUpperCase();
                clearTimeout(timer);
                if (controller) { controller.abort(); controller = null; }

                if (symbol.length < 1) {
                    render("", "");
                    input.removeAttribute("aria-invalid");
                    return;
                }
                render("looking", "…");
                timer = setTimeout(function () { lookup(symbol); }, 450);
            });
        })();

        /* --------------------------------------------- quantity checking
         * Inline, as they type. Mirrors the server's rules; the server
         * still has the final word on every one of them.
         */
        Array.prototype.forEach.call(document.querySelectorAll("[data-check-qty]"), function (input) {
            var status = document.getElementById(input.dataset.checkQty);
            if (!status) return;
            var max = parseFloat(input.getAttribute("max"));
            var maxLabel = input.dataset.maxLabel || "";

            input.addEventListener("input", function () {
                var raw = input.value.trim();
                if (!raw) {
                    status.dataset.state = ""; status.textContent = "";
                    input.removeAttribute("aria-invalid");
                    return;
                }
                var value = Number(raw);
                var message = "";
                if (!isFinite(value) || isNaN(value)) message = "That is not a number.";
                else if (value <= 0) message = "Enter an amount greater than zero.";
                else if (!isNaN(max) && value > max) message = "That is more than " + maxLabel + ".";

                if (message) {
                    input.setAttribute("aria-invalid", "true");
                    status.dataset.state = "bad";
                    status.textContent = message;
                } else {
                    input.setAttribute("aria-invalid", "false");
                    status.dataset.state = "ok";
                    var each = parseFloat(input.dataset.unitPrice);
                    status.textContent = isNaN(each) ? ""
                        : "≈ " + currency(value * each) + " at today's price";
                }
            });
        });

        /* ------------------------------------------------ sortable table
         * Sorts the rows already on the page. With JS off the table simply
         * keeps its server-side order, which is a sensible one.
         */
        Array.prototype.forEach.call(document.querySelectorAll("[data-sortable]"), function (table) {
            var head = table.tHead;
            var body = table.tBodies[0];
            if (!head || !body) return;

            Array.prototype.forEach.call(head.rows[0].cells, function (th, index) {
                if (th.dataset.sort === undefined) return;
                var button = document.createElement("button");
                button.type = "button";
                button.className = "sortable";
                button.innerHTML = th.innerHTML + ' <span class="caret" aria-hidden="true">↕</span>';
                th.textContent = "";
                th.appendChild(button);

                button.addEventListener("click", function () {
                    var ascending = th.getAttribute("aria-sort") !== "ascending";

                    Array.prototype.forEach.call(head.rows[0].cells, function (other) {
                        other.removeAttribute("aria-sort");
                    });
                    th.setAttribute("aria-sort", ascending ? "ascending" : "descending");

                    var rows = Array.prototype.slice.call(body.rows);
                    var numeric = th.dataset.sort === "number";
                    rows.sort(function (a, b) {
                        var x = key(a.cells[index], numeric);
                        var y = key(b.cells[index], numeric);
                        if (x < y) return ascending ? -1 : 1;
                        if (x > y) return ascending ? 1 : -1;
                        return 0;
                    });
                    rows.forEach(function (row) { body.appendChild(row); });
                });
            });

            function key(cell, numeric) {
                if (!cell) return numeric ? -Infinity : "";
                var raw = cell.dataset.value !== undefined
                    ? cell.dataset.value : cell.textContent;
                if (!numeric) return raw.trim().toLowerCase();
                var n = parseFloat(String(raw).replace(/[^0-9.\-]/g, ""));
                return isNaN(n) ? -Infinity : n;
            }
        });
    });

    function currency(n) {
        return "$" + n.toLocaleString(undefined, {
            minimumFractionDigits: 2, maximumFractionDigits: 2
        });
    }
    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function (ch) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
        });
    }
})();

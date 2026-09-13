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

    /* ------------------------------------------------------------ assistant
     * Turns the "Ask" link into a drawer. Without JavaScript, the link and
     * the form both go to the full /assistant page, so nothing here is
     * required.
     *
     * Model output is only ever placed with textContent. It is text from an
     * external service and must never be parsed as HTML.
     */
    document.addEventListener("DOMContentLoaded", function () {
        var drawer = document.querySelector("[data-assistant]");
        var toggle = document.querySelector("[data-assistant-toggle]");
        if (!drawer || !toggle || !("fetch" in window)) return;

        var log = drawer.querySelector("[data-assistant-log]");
        var form = drawer.querySelector("[data-assistant-form]");
        var field = form.querySelector("textarea");
        var submit = form.querySelector('button[type="submit"]');
        var starters = drawer.querySelector("[data-assistant-starters]");
        var closeButton = drawer.querySelector("[data-assistant-close]");
        var symbol = drawer.dataset.symbol || "";
        var STORE = "assistant-transcript";
        var transcript = load();
        var busy = false;

        transcript.forEach(function (turn) { render(turn.question, turn.answer, false); });
        if (transcript.length && starters) starters.hidden = true;

        function load() {
            try {
                var saved = JSON.parse(sessionStorage.getItem(STORE) || "[]");
                return Array.isArray(saved) ? saved.slice(-10) : [];
            } catch (e) { return []; }
        }
        function save() {
            try { sessionStorage.setItem(STORE, JSON.stringify(transcript.slice(-10))); }
            catch (e) { /* private mode or full: the drawer still works */ }
        }

        function open() {
            drawer.hidden = false;
            toggle.setAttribute("aria-expanded", "true");
            // One frame with the drawer displayed, so the slide has a start.
            requestAnimationFrame(function () { drawer.classList.add("is-open"); });
            field.focus();
            log.scrollTop = log.scrollHeight;
        }
        function close() {
            drawer.classList.remove("is-open");
            toggle.setAttribute("aria-expanded", "false");
            var done = function () { drawer.hidden = true; };
            if (reduced) done(); else setTimeout(done, 260);
            toggle.focus();
        }

        toggle.addEventListener("click", function (event) {
            event.preventDefault();
            if (drawer.hidden) open(); else close();
        });
        closeButton.addEventListener("click", close);
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !drawer.hidden) close();
        });

        if (starters) {
            Array.prototype.forEach.call(starters.querySelectorAll("[data-starter]"), function (button) {
                button.addEventListener("click", function () {
                    field.value = button.textContent.trim();
                    form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit"));
                });
            });
        }

        // Enter sends; Shift+Enter makes a new line.
        field.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit"));
            }
        });

        function paragraphs(container, text) {
            String(text).split(/\n\s*\n/).forEach(function (chunk) {
                if (!chunk.trim()) return;
                var p = document.createElement("p");
                p.textContent = chunk.trim();
                container.appendChild(p);
            });
        }

        function render(question, answer, isError) {
            var asked = document.createElement("div");
            asked.className = "turn turn-question";
            asked.textContent = question;
            log.appendChild(asked);

            var reply = document.createElement("div");
            reply.className = "turn " + (isError ? "turn-error" : "turn-answer");
            paragraphs(reply, answer);
            log.appendChild(reply);
            log.scrollTop = log.scrollHeight;
            return reply;
        }

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            var question = field.value.trim();
            if (!question || busy) return;

            busy = true;
            if (starters) starters.hidden = true;
            field.value = "";
            submit.disabled = true;
            field.setAttribute("aria-busy", "true");

            var asked = document.createElement("div");
            asked.className = "turn turn-question";
            asked.textContent = question;
            log.appendChild(asked);

            var pending = document.createElement("div");
            pending.className = "turn turn-pending";
            pending.innerHTML = '<span class="spinner" aria-hidden="true"></span>';
            pending.appendChild(document.createTextNode(" Thinking…"));
            log.appendChild(pending);
            log.scrollTop = log.scrollHeight;

            fetch("/api/assistant", {
                method: "POST",
                headers: { "Content-Type": "application/json", "Accept": "application/json" },
                body: JSON.stringify({
                    question: question,
                    symbol: symbol,
                    earlier: transcript.slice(-3)
                })
            })
                .then(function (res) {
                    return res.json().catch(function () {
                        return { ok: false, message: "The app sent back something unexpected. Try again." };
                    });
                })
                .then(function (data) {
                    pending.remove();
                    var reply = document.createElement("div");
                    if (data.ok) {
                        reply.className = "turn turn-answer";
                        paragraphs(reply, data.answer);
                        transcript.push({ question: question, answer: data.answer });
                        save();
                    } else {
                        reply.className = "turn turn-error";
                        paragraphs(reply, data.message || "No answer this time. Try again.");
                    }
                    log.appendChild(reply);
                })
                .catch(function () {
                    pending.remove();
                    var reply = document.createElement("div");
                    reply.className = "turn turn-error";
                    paragraphs(reply, "Could not reach the app. Check the server is still running, then try again.");
                    log.appendChild(reply);
                })
                .then(function () {
                    busy = false;
                    submit.disabled = false;
                    field.removeAttribute("aria-busy");
                    log.scrollTop = log.scrollHeight;
                    field.focus();
                });
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

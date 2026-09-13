/* Progressive enhancement.
 *
 * Every behaviour here is an improvement on something that already works
 * without it. With JavaScript off: the table still renders (just
 * unsorted), the ticker is still validated (by the server, on submit),
 * forms still submit, the chart still draws, Ask opens as a full page, and
 * nothing is hidden. The server remains the only authority on what is
 * valid; these checks catch a mistake a few seconds earlier, never decide.
 */
(function () {
    "use strict";

    var root = document.documentElement;
    var reduced = window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* ------------------------------------------------------ scroll reveal
     * Chrome and Edge do this in CSS with animation-timeline and this is
     * skipped entirely. Safari and Firefox get an IntersectionObserver,
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

    function each(selector, fn, scope) {
        Array.prototype.forEach.call((scope || document).querySelectorAll(selector), fn);
    }

    document.addEventListener("DOMContentLoaded", function () {

        /* -------------------------------------------------- busy buttons
         * A form that hits the network must never look frozen. The button
         * keeps its width so the layout does not jump.
         */
        each("form[data-busy-label]", function (form) {
            form.addEventListener("submit", function () {
                var btn = form.querySelector('button[type="submit"], button:not([type])');
                if (!btn || btn.dataset.busy === "true") return;
                // Let native validation block submission before we commit.
                if (typeof form.checkValidity === "function" && !form.checkValidity()) return;

                btn.style.minWidth = btn.offsetWidth + "px";
                btn.dataset.busy = "true";
                btn.setAttribute("aria-busy", "true");
                btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>'
                    + escapeHtml(form.dataset.busyLabel);
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
        each("[data-check-qty]", function (input) {
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
                    var unit = parseFloat(input.dataset.unitPrice);
                    status.textContent = isNaN(unit) ? ""
                        : "≈ " + currency(value * unit) + " at today's price";
                }
            });
        });

        /* ------------------------------------------------ sortable table
         * Sorts the rows already on the page. With JS off the table simply
         * keeps its server-side order, which is a sensible one.
         */
        each("[data-sortable]", function (table) {
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

        /* -------------------------------------------------- row links
         * The whole holdings row opens the stock. Keyboard users already
         * have the real link in the first cell, so this is pointer-only.
         */
        each("tr[data-href]", function (row) {
            row.addEventListener("click", function (event) {
                if (event.target.closest("a, button")) return;
                if (window.getSelection && String(window.getSelection())) return;
                window.location.href = row.dataset.href;
            });
        });

        /* ------------------------------------------------ chart readout
         * A crosshair and tooltip that follow the pointer, the same readout
         * from the arrow keys, and a table of the plotted prices. The chart
         * and its text description are complete without any of this.
         */
        each("[data-chart]", function (figure) {
            var plot = figure.querySelector("[data-chart-plot]");
            var source = figure.querySelector("[data-chart-points]");
            if (!plot || !source) return;
            var points;
            try { points = JSON.parse(source.textContent); } catch (e) { return; }
            if (!points || points.length < 2) return;

            var layer = document.createElement("div");
            layer.className = "readout";
            layer.hidden = true;
            layer.setAttribute("aria-hidden", "true");
            layer.innerHTML = '<span class="readout-line"></span><span class="readout-dot"></span>'
                + '<span class="readout-tip"><span class="readout-when"></span><span class="readout-price"></span></span>';
            plot.appendChild(layer);
            var line = layer.children[0], dot = layer.children[1], tip = layer.children[2];

            var live = document.createElement("p");
            live.className = "visually-hidden";
            live.setAttribute("aria-live", "polite");
            figure.appendChild(live);

            plot.tabIndex = 0;
            plot.setAttribute("aria-label", "Price chart. Use the left and right arrow keys to read prices.");
            var current = points.length - 1;

            function show(index, announce) {
                current = Math.max(0, Math.min(points.length - 1, index));
                var p = points[current];
                layer.hidden = false;
                line.style.left = p[0] + "%";
                dot.style.left = p[0] + "%";
                dot.style.top = p[1] + "%";
                tip.children[0].textContent = p[2];
                tip.children[1].textContent = p[3];
                // Keep the tooltip on the plot: flip it left past the middle.
                var right = p[0] > 55;
                tip.style.left = right ? "" : "calc(" + p[0] + "% + 12px)";
                tip.style.right = right ? "calc(" + (100 - p[0]) + "% + 12px)" : "";
                if (announce) live.textContent = p[2] + ", " + p[3];
            }
            function hide() { layer.hidden = true; }

            function nearest(clientX) {
                var box = plot.getBoundingClientRect();
                var pct = (clientX - box.left) / box.width * 100;
                var lo = 0, hi = points.length - 1;
                while (hi - lo > 1) {
                    var mid = (lo + hi) >> 1;
                    if (points[mid][0] < pct) lo = mid; else hi = mid;
                }
                return Math.abs(points[lo][0] - pct) <= Math.abs(points[hi][0] - pct) ? lo : hi;
            }

            plot.addEventListener("pointermove", function (e) { show(nearest(e.clientX), false); });
            plot.addEventListener("pointerleave", function () {
                if (document.activeElement !== plot) hide();
            });
            plot.addEventListener("focus", function () { show(current, true); });
            plot.addEventListener("blur", hide);
            plot.addEventListener("keydown", function (e) {
                var step = e.shiftKey ? 10 : 1;
                var moves = { ArrowLeft: current - step, ArrowRight: current + step, Home: 0, End: points.length - 1 };
                if (!(e.key in moves)) return;
                e.preventDefault();
                show(moves[e.key], true);
            });

            var tools = figure.querySelector("[data-chart-tools]");
            var toggle = figure.querySelector("[data-chart-table-toggle]");
            var holder = figure.querySelector("[data-chart-table]");
            if (!tools || !toggle || !holder) return;
            tools.hidden = false;

            toggle.addEventListener("click", function () {
                var opening = holder.hidden;
                if (opening && !holder.firstChild) {
                    var table = document.createElement("table");
                    var caption = document.createElement("caption");
                    caption.className = "visually-hidden";
                    caption.textContent = "Prices plotted on the chart, newest first";
                    table.appendChild(caption);
                    var head = table.createTHead().insertRow();
                    ["Date", "Price"].forEach(function (label, i) {
                        var th = document.createElement("th");
                        th.scope = "col";
                        th.textContent = label;
                        if (i) th.className = "r";
                        head.appendChild(th);
                    });
                    var body = table.createTBody();
                    for (var i = points.length - 1; i >= 0; i--) {
                        var row = body.insertRow();
                        row.insertCell().textContent = points[i][2];
                        var cell = row.insertCell();
                        cell.className = "r num";
                        cell.textContent = points[i][3];
                    }
                    holder.appendChild(table);
                }
                holder.hidden = !opening;
                toggle.setAttribute("aria-expanded", String(opening));
                toggle.textContent = opening ? "Hide table" : "View as table";
            });
        });
    });

    /* ------------------------------------------------------------ assistant
     * Turns the "Ask" link into a research panel. Without JavaScript, the
     * link and every form go to the full /assistant page, so nothing here
     * is required.
     *
     * Model output is only ever placed with textContent. It is text from an
     * external service and must never be parsed as HTML.
     */
    document.addEventListener("DOMContentLoaded", function () {
        var drawer = document.querySelector("[data-assistant]");
        var toggle = document.querySelector("[data-assistant-toggle]");
        if (!drawer || !toggle || !("fetch" in window)) return;

        var log = drawer.querySelector("[data-assistant-log]");
        var intro = drawer.querySelector("[data-assistant-intro]");
        var form = drawer.querySelector("[data-assistant-form]");
        var field = form.querySelector("textarea");
        var submit = form.querySelector('button[type="submit"]');
        var starters = drawer.querySelector("[data-assistant-starters]");
        var closeButton = drawer.querySelector("[data-assistant-close]");
        var counter = drawer.querySelector("[data-composer-count]");
        var researchLabel = drawer.querySelector("[data-research-label]");
        var symbol = drawer.dataset.symbol || "";
        var MAX = parseInt(field.getAttribute("maxlength"), 10) || 1000;
        var narrow = window.matchMedia ? window.matchMedia("(max-width: 720px)") : { matches: false };
        var STORE = "assistant-transcript";
        var transcript = load();
        var busy = false;
        var opener = null;

        // A restored answer keeps its sources and Google's suggestions: the
        // terms require the suggestions to accompany a searched answer
        // whenever it is shown, not just the first time.
        transcript.forEach(function (turn) {
            questionTurn(turn.question);
            var reply = answerTurn(turn.answer, turn.notice);
            grounding(reply, turn.sources, turn.suggestions, turn.searches);
            answerFoot(reply, turn.question, turn.answer);
        });
        if (transcript.length) quiet();

        function load() {
            try {
                var saved = JSON.parse(sessionStorage.getItem(STORE) || "[]");
                return Array.isArray(saved) ? saved.slice(-10) : [];
            } catch (e) { return []; }
        }
        function save() {
            try { sessionStorage.setItem(STORE, JSON.stringify(transcript.slice(-10))); }
            catch (e) { /* private mode or full: the panel still works */ }
        }
        function quiet() {
            if (starters) starters.hidden = true;
            if (intro) intro.hidden = true;
        }
        function scrollDown() { log.scrollTop = log.scrollHeight; }

        function setResearch(on) {
            drawer.dataset.research = on ? "on" : "off";
            if (researchLabel) researchLabel.textContent = on ? "Web research on" : "Web research off";
        }

        /* ---- open, close, focus */
        function open(from) {
            opener = from || toggle;
            drawer.hidden = false;
            toggle.setAttribute("aria-expanded", "true");
            // One frame with the panel displayed, so the slide has a start.
            requestAnimationFrame(function () { drawer.classList.add("is-open"); });
            field.focus();
            scrollDown();
        }
        function close() {
            drawer.classList.remove("is-open");
            toggle.setAttribute("aria-expanded", "false");
            var done = function () { drawer.hidden = true; };
            if (reduced) done(); else setTimeout(done, 200);
            (opener && document.contains(opener) ? opener : toggle).focus();
        }

        toggle.addEventListener("click", function (event) {
            event.preventDefault();
            if (drawer.hidden) open(toggle); else close();
        });
        each("[data-assistant-open]", function (link) {
            link.addEventListener("click", function (event) {
                event.preventDefault();
                open(link);
            });
        });
        closeButton.addEventListener("click", close);

        document.addEventListener("keydown", function (event) {
            if (drawer.hidden) return;
            if (event.key === "Escape") { close(); return; }
            // Full screen on a phone is a dialog, so focus stays inside it.
            // Beside the page on a desktop it is not, and Tab may leave.
            if (event.key !== "Tab" || !narrow.matches) return;
            var focusable = drawer.querySelectorAll(
                'a[href], button:not([disabled]), textarea:not([disabled]), summary, iframe');
            var visible = Array.prototype.filter.call(focusable, function (el) { return el.offsetParent !== null; });
            if (!visible.length) return;
            var first = visible[0], last = visible[visible.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        });

        if (starters) {
            each("[data-starter]", function (button) {
                button.addEventListener("click", function () { ask(button.textContent.trim()); });
            }, starters);
        }

        // Suggested questions on a stock page are real forms; send them here.
        each("[data-ask-starters]", function (starterForm) {
            starterForm.addEventListener("submit", function (event) {
                var chosen = event.submitter;
                if (!chosen || !chosen.value) return;
                event.preventDefault();
                open(chosen);
                ask(chosen.value);
            });
        });

        // Enter sends; Shift+Enter makes a new line.
        field.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit"));
            }
        });
        field.addEventListener("input", function () {
            if (!counter) return;
            var left = MAX - field.value.length;
            counter.hidden = field.value.length < MAX * 0.9;
            counter.textContent = "· " + left + " characters left";
            counter.classList.toggle("is-near", left < 25);
        });

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            ask(field.value.trim());
        });

        /* ---- rendering */
        function paragraphs(container, text) {
            String(text).split(/\n\s*\n/).forEach(function (chunk) {
                if (!chunk.trim()) return;
                var p = document.createElement("p");
                p.textContent = chunk.trim();
                container.appendChild(p);
            });
        }

        function el(tag, className, text) {
            var node = document.createElement(tag);
            if (className) node.className = className;
            if (text !== undefined) node.textContent = text;
            return node;
        }

        function questionTurn(question) {
            quiet();
            log.appendChild(el("div", "turn turn-question", question));
        }

        function answerTurn(answer, notice) {
            var reply = el("div", "turn turn-answer");
            if (notice) {
                // Above the answer, so nobody reads it as researched first.
                var note = el("p", "answer-notice");
                note.setAttribute("role", "note");
                note.appendChild(el("strong", "", "Not researched"));
                note.appendChild(document.createTextNode(notice));
                reply.appendChild(note);
            }
            paragraphs(reply, answer);
            log.appendChild(reply);
            scrollDown();
            return reply;
        }

        /* Where the answer came from: the searches it ran, its sources, and
         * Google Search suggestions. Suggestions are Google's own HTML,
         * shown unmodified as its terms require, inside a sandboxed iframe:
         * no scripts, and its CSS can't touch this page. Source links go
         * straight to the address Google gave, and only http(s) addresses
         * are ever made into links. */
        function grounding(container, sources, suggestions, searches) {
            sources = (sources || []).filter(function (src) {
                return src && /^https?:\/\//i.test(String(src.uri || ""));
            });
            searches = searches || [];
            if (!sources.length && !suggestions && !searches.length) return;

            var box = el("div", "provenance");
            var head = el("div", "provenance-head");
            head.appendChild(el("span", "answer-badge is-researched", "Researched"));
            if (sources.length) {
                head.appendChild(el("span", "", sources.length + (sources.length === 1 ? " source" : " sources")));
            }
            box.appendChild(head);

            if (searches.length) {
                var searched = el("p", "searches", "Searched Google for ");
                searches.forEach(function (query, i) {
                    if (i) searched.appendChild(document.createTextNode(", "));
                    searched.appendChild(el("q", "", query));
                });
                box.appendChild(searched);
            }

            if (sources.length) {
                var wrap = el("div", "sources");
                var list = document.createElement("ol");
                sources.forEach(function (src) {
                    var item = document.createElement("li");
                    var link = document.createElement("a");
                    link.href = src.uri;
                    link.target = "_blank";
                    link.rel = "noopener";
                    link.textContent = src.title || src.uri;
                    item.appendChild(link);
                    if (src.domain && src.domain !== src.title) {
                        item.appendChild(el("span", "source-domain", " " + src.domain));
                    }
                    list.appendChild(item);
                });
                wrap.appendChild(list);
                box.appendChild(wrap);
            }

            if (suggestions) {
                var frame = document.createElement("iframe");
                frame.className = "search-suggestions";
                frame.title = "Related Google searches";
                frame.setAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
                frame.srcdoc = '<!doctype html><html><head><meta charset="utf-8">'
                    + '<base target="_blank"></head><body style="margin:0">'
                    + suggestions + "</body></html>";
                box.appendChild(frame);
            }
            container.appendChild(box);
        }

        function answerFoot(container, question, answer) {
            var foot = el("div", "answer-foot");
            foot.appendChild(el("span", "", "AI-generated · can be wrong · not financial advice"));
            var tools = el("span", "answer-tools");

            var copy = el("button", "", "Copy");
            copy.type = "button";
            copy.addEventListener("click", function () {
                var done = function () {
                    copy.textContent = "Copied";
                    setTimeout(function () { copy.textContent = "Copy"; }, 2000);
                };
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(answer).then(done, function () {});
                }
            });

            var again = el("button", "", "Ask again");
            again.type = "button";
            again.addEventListener("click", function () { ask(question); });

            tools.appendChild(copy);
            tools.appendChild(again);
            foot.appendChild(tools);
            container.appendChild(foot);
        }

        /* A failure the reader can act on: what happened, and a way forward. */
        var TITLES = {
            invalid: "Check your question",
            busy: "Ask needs a short break",
            not_configured: "Ask isn't available on this server",
            disabled: "Ask is switched off",
            refused: "Ask can't help with that one",
            failed: "Ask couldn't answer that",
            network: "Couldn't reach the app",
            aborted: "Stopped"
        };

        function failure(question, kind, message, retryAfter) {
            var box = el("div", "turn turn-error" + (kind === "failed" || kind === "network" ? "" : " is-info"));
            box.setAttribute("role", kind === "failed" || kind === "network" ? "alert" : "status");
            box.appendChild(el("strong", "", TITLES[kind] || TITLES.failed));
            var text = el("p", "", message);
            box.appendChild(text);

            if (kind === "busy" && retryAfter) {
                var left = retryAfter;
                var tick = function () {
                    if (left <= 0) { text.textContent = "You can ask again now."; return; }
                    var minutes = Math.floor(left / 60), seconds = left % 60;
                    text.textContent = message + " Try again in "
                        + (minutes ? minutes + " min " : "") + seconds + " s.";
                    left -= 1;
                    setTimeout(tick, 1000);
                };
                tick();
            }
            if (kind === "failed" || kind === "network" || kind === "aborted") {
                var retry = el("button", "btn btn-secondary btn-sm", "Try again");
                retry.type = "button";
                retry.addEventListener("click", function () { ask(question); });
                box.appendChild(retry);
            }
            if (kind === "disabled") {
                toggle.hidden = true;
                each("[data-assistant-open], [data-ask-starters]", function (node) { node.hidden = true; });
            }
            log.appendChild(box);
            scrollDown();
        }

        /* The wait, described honestly by elapsed time. The app cannot see
         * which step the model is on, so it never claims one. */
        function waiting() {
            var box = el("div", "turn turn-pending");
            box.hidden = true;
            var line = el("div", "pending-line");
            line.innerHTML = '<span class="spinner" aria-hidden="true"></span>';
            var words = el("span", "", "Researching…");
            var clock = el("span", "pending-elapsed");
            line.appendChild(words);
            line.appendChild(clock);
            box.appendChild(line);
            var skeleton = el("div", "skeleton");
            skeleton.setAttribute("aria-hidden", "true");
            skeleton.innerHTML = "<i></i><i></i><i></i>";
            box.appendChild(skeleton);
            log.appendChild(box);

            var started = Date.now(), stage = -1, cancel = null, timer;
            function update() {
                var seconds = Math.floor((Date.now() - started) / 1000);
                var next = seconds < 1 ? 0 : seconds < 5 ? 1 : seconds < 20 ? 2 : seconds < 45 ? 3 : 4;
                if (next >= 1) box.hidden = false;
                if (seconds >= 20) clock.textContent = seconds + " s";
                if (next !== stage) {
                    stage = next;
                    if (stage === 1) words.textContent = "Researching…";
                    if (stage === 2) {
                        words.textContent = drawer.dataset.research === "on"
                            ? "Searching the web and reading sources. This usually takes 10 to 20 seconds."
                            : "Reading your account and stored prices…";
                    }
                    if (stage === 3) words.textContent = "Still working. Questions about recent news take longer.";
                    if (stage === 4) {
                        words.textContent = "Taking longer than usual.";
                        if (cancel) box.appendChild(cancel);
                    }
                    scrollDown();
                }
            }
            timer = setInterval(update, 500);
            update();
            return {
                onCancel: function (fn) {
                    cancel = el("button", "btn btn-secondary btn-sm", "Cancel");
                    cancel.type = "button";
                    cancel.addEventListener("click", fn);
                },
                done: function () { clearInterval(timer); box.remove(); }
            };
        }

        /* ---- asking */
        function ask(question) {
            if (!question || busy) return;
            if (question.length > MAX) {
                failure(question, "invalid", "That question is too long. Keep it under " + MAX + " characters.");
                return;
            }

            busy = true;
            field.value = "";
            if (counter) counter.hidden = true;
            submit.disabled = true;
            log.setAttribute("aria-busy", "true");
            questionTurn(question);

            var controller = window.AbortController ? new AbortController() : null;
            var pending = waiting();
            if (controller) pending.onCancel(function () { controller.abort(); });
            scrollDown();

            fetch("/api/assistant", {
                method: "POST",
                signal: controller ? controller.signal : undefined,
                headers: { "Content-Type": "application/json", "Accept": "application/json" },
                body: JSON.stringify({
                    question: question,
                    symbol: symbol,
                    // Only the words go back; sources and Google's HTML stay here.
                    earlier: transcript.slice(-3).map(function (turn) {
                        return { question: turn.question, answer: turn.answer };
                    })
                })
            })
                .then(function (res) {
                    return res.json().catch(function () {
                        return { ok: false, kind: "failed", message: "The app sent back something unexpected. Try again." };
                    });
                })
                .then(function (data) {
                    pending.done();
                    if (typeof data.research === "boolean") setResearch(data.research);
                    if (data.ok) {
                        var reply = answerTurn(data.answer, data.notice);
                        grounding(reply, data.sources, data.search_suggestions, data.searches);
                        answerFoot(reply, question, data.answer);
                        transcript.push({
                            question: question,
                            answer: data.answer,
                            sources: data.sources || [],
                            suggestions: data.search_suggestions || "",
                            searches: data.searches || [],
                            notice: data.notice || ""
                        });
                        save();
                    } else {
                        if (data.kind === "invalid") field.value = question;
                        failure(question, data.kind || "failed",
                            data.message || "No answer this time. Try again.", data.retry_after);
                    }
                })
                .catch(function (err) {
                    pending.done();
                    field.value = question;
                    if (err && err.name === "AbortError") {
                        failure(question, "aborted", "The question was cancelled. It's back in the box if you want to send it again.");
                    } else {
                        failure(question, "network", "Check the server is still running, then try again.");
                    }
                })
                .then(function () {
                    busy = false;
                    submit.disabled = false;
                    log.removeAttribute("aria-busy");
                    scrollDown();
                    if (!drawer.hidden) field.focus();
                });
        }
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

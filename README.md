# Stock Trading Simulator with AI Assistant

A Python + PostgreSQL paper-trading app: buy and sell real stocks at live market prices with practice money, then ask an AI research assistant about any stock or your own portfolio. Built as a hands-on project to practice relational database design, API integration, and secure data handling.

It has two front ends — a terminal menu and a Flask web interface — sharing one set of models and one operations layer, so both behave identically. Accounts sign in with Google, and each one gets a $50,000 practice cash balance to trade with. The web app adds stock pages with 1D-to-all-time price charts and Ask, an assistant powered by Google's Gemini that is grounded in the app's own data and can research stocks with Google Search.

## Features

- Pulls live stock prices from Yahoo Finance via the `yfinance` API
- Stores portfolio holdings and stock data in a PostgreSQL database with proper relational structure (foreign keys linking holdings to stock reference data)
- Calculates real-time gain/loss per holding, with cost basis and portfolio totals
- Repeat buys of the same ticker merge into a single position at a weighted-average cost
- Exact decimal money handling end to end — prices and share counts never pass through binary floats
- Secure credential handling using environment variables (`.env`, git-ignored)
- Defensive error handling — a failed API call or missing data for one stock doesn't crash the whole run
- Transactional database updates with rollback on failure, ensuring the database is never left in a partially-updated state
- All SQL queries use parameterized statements to prevent SQL injection
- Two interfaces over the same logic: a terminal menu and a browser UI, neither duplicating the other's rules
- Google sign-in (OpenID Connect via Authlib); accounts are keyed on Google's stable `sub` claim, not the email
- Per-account cash balance: buys debit it, sells credit it, and a purchase that exceeds it is refused inside the database transaction
- Every holding is scoped to its owner, enforced by a `UNIQUE (user_id, symbol)` constraint rather than by convention

## Tech Stack

- **Python** — application logic, API integration
- **PostgreSQL** — relational data storage
- **psycopg2** — Python/PostgreSQL database driver
- **yfinance** — live market data
- **python-dotenv** — environment variable management
- **Flask** — server-rendered web interface (Jinja templates, plain CSS, no build step)
- **Authlib** — Google OAuth / OpenID Connect
- **Google Gemini** (`google-genai`) — the Ask research assistant
- **pytest** — test suite

## Database Schema

- `users` — one row per account: Google's `sub` claim, email, display name, and cash balance
- `stocks` — reference data per ticker (symbol, company name, latest price), shared by all accounts
- `portfolio` — holdings (owner, symbol, shares, purchase price). `UNIQUE (user_id, symbol)`: one row per ticker *per account*, so a repeat buy blends into a weighted-average cost basis rather than opening a second lot. The `user_id` in that constraint is load-bearing — a bare `UNIQUE (symbol)` would collide across accounts and hand one person another person's position.
- `price_history` — daily OHLCV data per ticker, with a composite unique constraint on `(symbol, date)` so ingestion is safe to re-run

Money columns are `NUMERIC` and carry `CHECK` constraints that reject negative and `NaN` values. (PostgreSQL sorts `'NaN'::numeric` above every other numeric, so `shares > 0` alone does not exclude it — the constraints spell out `<> 'NaN'` explicitly.)

## Architecture

```
portfolio_tracker/
    config.py              credentials from .env
    db.py                  connection + transaction helper
    models/                all SQL lives here
        stocks.py          ticker reference data
        portfolio.py       holdings
        history.py         daily OHLCV
        users.py           accounts and cash balances
    services/
        market_data.py     the only module that talks to yfinance
    errors.py              domain errors shared across layers
    operations.py          shared logic: validate, price, buy, sell, refresh
    reports.py             terminal rendering
    cli.py                 terminal front end
web.py                     web front end (routes + templates)
templates/                 Jinja templates
static/style.css           all styling, including the motion layer
static/reveal.js           scroll-reveal fallback for Safari and Firefox
```

The rule is one-directional: routes and menu handlers call `operations`, which
calls `models` and `services`. No SQL and no market-data calls appear in a route
handler or a CLI prompt. `operations.py` returns plain values and never prints or
renders, which is what lets the terminal and the browser share it without either
one bending to suit the other.

## Design

The front end aims for the calm of a full-service broker rather than the
urgency of a trading app: **numbers lead, and chrome recedes.** White panels
on a cool grey ground, hairline dividers, one navy accent, and IBM Plex Sans
with tabular numerals so figures never shift as they change. The fonts are
served from `static/fonts/` (SIL Open Font License), not a font CDN.

- **Colour has jobs.** Navy is for links, focus and the chart line. Gold is
  reserved for Ask, the research assistant, so it is recognisable anywhere.
  Green and red belong to data only: Buy is ink and Sell is outlined, never
  coloured like a result.
- **Direction is never colour alone.** Every change carries a sign, an arrow
  and a word, at the same weight for gains and losses, so it reads in
  greyscale and to a screen reader. There is no confetti anywhere: buying
  should not feel like winning.
- **Contrast is measured, not eyeballed.** Every text colour meets WCAG AA on
  the surface it sits on. Dividers are decorative; inputs and outlined buttons
  use a separate, darker border that meets the 3:1 minimum.
- **Light and dark themes** follow the operating system setting.

The dashboard leads with four figures (account value, investments, total gain
or loss, cash), then holdings beside an allocation list. There is no "today's
change" figure yet: the app does not store a previous close.

The landing page carries one WebGL scene, a drifting point field whose colours
come from the stylesheet, with all geometry generated in `static/hero.js`. It
sits behind a finished static background and declines to run at all on
low-core or low-memory devices, on small touch screens, under Save-Data,
without WebGL, or under reduced motion. It stops rendering when the tab is
hidden or the hero scrolls out of view. Three.js is pinned to r128 with a
subresource-integrity hash and loads on that page only.

## Stock pages and charts

Every ticker links to `/stock/<symbol>`: the live price, your position, and a
price chart over 1D, 5D, 1M, 3M, 6M, 1Y or all time. Charts are inline SVG
drawn on the server — no charting library, and they work with JavaScript off.
Each chart carries a text description, and the figures beside it repeat what
the line shows.

With JavaScript on, a crosshair and tooltip give the exact date and price under
the pointer, the arrow keys step through the same readout once the chart is
focused, and "View as table" lists the plotted prices.

Daily ranges read `price_history`. 1D and 5D need intraday bars, which that
table cannot hold (it is one row per day by design), so they use
`price_intraday` — apply `migrations/003_intraday_prices.sql` to an existing
database. "1 day" means the most recent trading session, so it still shows
Friday's prices over a weekend. Long ranges are thinned to 720 drawn points,
keeping each span's high and low; every printed figure uses the full series.

## Assistant

A panel on every signed-in page answers questions about the stock you are
looking at, or about your portfolio. It runs on Google's Gemini
(`gemini-3.8-flash`) through the official `google-genai` SDK, from
`portfolio_tracker/services/assistant.py` — the only module that talks to the
API.

- **Your own figures come from the app.** Each question is sent with a data
  block built server-side — price, your position, stored history, your
  holdings. The browser only says which ticker the page shows; no figure it
  sends reaches the model.
- **It researches the rest with Google Search.** News, earnings, what a company
  does — looked up rather than recalled, with the sources listed as links
  under the answer. Where the web and the app disagree about your own
  figures, the app wins.
- **It explains and does not advise.** It will not tell you to buy, sell or
  hold, predict prices, or call something a good investment. Analyst views,
  if it mentions them, are framed as opinions that often disagree.
- **It shows its work.** Each answer is marked Researched or Not researched,
  lists the Google searches it ran and its sources, and the panel's "What Ask
  can see" list names exactly what is sent. While waiting, the panel says what
  is happening by elapsed time (searching only when search is available) and
  offers Cancel after 45 seconds. Each failure (rate limit, not set up,
  refused, network) gets its own message and a way forward.

### Setting it up

Add `GEMINI_API_KEY` to `.env` — create one at
[aistudio.google.com/apikey](https://aistudio.google.com/apikey) — and restart.
The SDK also accepts `GOOGLE_API_KEY`; `GEMINI_API_KEY` is suggested only so it
is not confused with the `GOOGLE_CLIENT_*` sign-in settings.

**Web research needs a paid key.** Grounding with Google Search is not on the
Gemini free tier. On a free key the assistant still answers, using only the
app's data, and every such answer carries a note saying research was not
available. With billing enabled on the key's Google Cloud project, research
turns on by itself within 15 minutes — no restart. Paid pricing at the time of
writing: 5,000 searches a month free across Gemini 3 models, then $14 per
1,000. Set `ASSISTANT_SEARCH=0` to switch research off.

**Privacy.** On a free key Google may use what you send to improve its
products and human reviewers may read it; a paid key does not use prompts
that way. The assistant never sends your name or email, and the panel reminds
people to leave personal details out of questions.

### Google's display terms for searched answers

Answers that used Google Search come with Google's search-suggestion chips,
which its terms require to be shown with the answer and left unmodified. They
are rendered exactly as returned, inside a sandboxed iframe (no scripts, and
their CSS cannot reach the page). Source links point at the exact address
Google returned, with no redirect or click tracking added.

### How failures are handled

- A search refused for quota (HTTP 429) is answered again without search, and
  search is paused for 15 minutes rather than refused on every question.
- Temporary server errors (Gemini's "high demand" 503s) are retried twice with
  short backoff before the reader sees anything.
- An invalid key — which Gemini reports as HTTP 400, not 401 — is recognised
  and explained; so are a missing key, an unknown model and network failures.
- Refused or safety-blocked answers are withheld rather than shown partially.
- Each account is limited to 20 questions per 10 minutes.

With JavaScript off, the "Ask" button opens a plain page that does the same
thing. The test suite blocks any real API call.

## Motion

Scroll-driven animation is done natively with CSS `animation-timeline: view()`
and `scroll()`, behind `@supports`. Chrome and Edge run it in the compositor
with no JavaScript at all; `static/reveal.js` adds an IntersectionObserver
fallback for Safari and Firefox, and bows out entirely where the CSS already
works.

Three rules hold it together:

- **Content is never gated.** Every element's default state is its *final*
  state. The only rules that set `opacity: 0` live under `.js-reveal`, a class
  that JavaScript adds only after confirming it can finish the job — so with
  JavaScript off, nothing is ever hidden.
- **`prefers-reduced-motion: reduce` disables all of it**, including smooth
  scrolling, and pins every element to its finished position.
- **Only `transform` and `opacity` animate.** Nothing touches width, height,
  top or margin, which force reflow and drop frames.

Intensity is set per page via `data-motion` on `<body>`: `lively` on the
landing and sign-in pages, `calm` on the portfolio, balance and history views
where numbers are being read. Forms are deliberately excluded — nothing moves
while you are filling one in.

## Setup

1. Clone the repo.

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create a PostgreSQL database and apply the schema:

   ```bash
   createdb -U postgres practice
   psql -U postgres -d practice -f schema.sql
   ```

4. Create a `.env` file in the project root:

   ```
   DB_PASSWORD=your_postgres_password

   GOOGLE_CLIENT_ID=your_client_id
   GOOGLE_CLIENT_SECRET=your_client_secret

   PORTFOLIO_USER=you@gmail.com
   ```

   Optionally override `DB_HOST`, `DB_NAME`, `DB_USER`, or `DB_PORT` — they default to `localhost`, `practice`, `postgres`, and `5432`. `STARTING_CASH` defaults to `50000`, and `FLASK_SECRET_KEY` keeps flash messages working across restarts.

   `.env` is git-ignored, and nothing above is ever hardcoded — it all arrives through `config.py`.

### Setting up Google sign-in

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project.
2. **APIs & Services → OAuth consent screen** → External. Add your own email under *Test users*, otherwise Google blocks the sign-in while the app is unverified.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID → Web application.**
4. Under *Authorized redirect URIs*, add exactly:

   ```
   http://127.0.0.1:5000/auth/callback
   ```

   It must match character for character, `127.0.0.1` included — `localhost` is a different origin to Google.
5. Copy the client ID and secret into `.env`, then restart the server.

The sign-in page tells you all of this, with your actual redirect URI filled in, whenever the credentials are missing.

**Trying it without Google.** Set `ALLOW_DEV_LOGIN=1` to enable a local sign-in form that accepts any email address and verifies nothing. It exists so you can use the app before creating a Cloud Console project. It is off unless that variable is set, and the page says plainly when it is on — unset it when you're done.

5. Run whichever interface you prefer — they share the same database and logic.

   **Terminal:**

   ```bash
   python main.py
   ```

   or equivalently `python -m portfolio_tracker`.

   The terminal cannot run an OAuth browser redirect, so it reads
   `PORTFOLIO_USER` from `.env` to decide whose portfolio to open. Sign in
   through the web app once first; if the setting is missing or does not
   match an account, the CLI lists the accounts it does know about.

   **Web:**

   ```bash
   python -m flask --app web run
   ```

   Then open <http://127.0.0.1:5000>. Add `--debug` for auto-reload while
   editing, or `--port 5001` if 5000 is taken. `python web.py` also works.

   Use `python -m flask`, not a bare `flask`. If several Pythons are
   installed (an Anaconda alongside a python.org one, say), `flask.exe`
   may resolve to a different interpreter than `python` does — and an
   older Flask there fails with `Error: No such option: --app`, since
   `--app` needs Flask 2.2 or newer. The `python -m` form always uses the
   same interpreter as `python`, so it cannot drift.

   Optionally set `FLASK_SECRET_KEY` in `.env` to keep flash messages working
   across restarts; without it a random key is generated per process.

## Upgrading an existing database

`schema.sql` uses `CREATE TABLE IF NOT EXISTS`, so it will not alter tables that already exist. A database created before the one-row-per-symbol change needs migration 001:

```bash
psql -U postgres -d practice -f migrations/001_one_row_per_symbol.sql
```

It merges any duplicate lots into a weighted-average position, then adds the `UNIQUE`, `NOT NULL`, and `CHECK` constraints.

Adding accounts and cash needs migration 002 as well:

```bash
psql -U postgres -d practice -f migrations/002_accounts_and_cash.sql
```

It creates `users`, gives `portfolio` a `user_id`, and swaps `UNIQUE (symbol)` for `UNIQUE (user_id, symbol)`. Any holdings already in the table are adopted by a placeholder account (`legacy@localhost`) so nothing is orphaned — sign in with Google afterwards and move them across, or delete that account once you no longer need it.

Both migrations are safe to re-run.

## Tests

```bash
pip install pytest
pytest
```

Database tests run against a throwaway database (`portfolio_test` by default, override with `TEST_DB_NAME`) and never touch the application database. They skip automatically if PostgreSQL isn't reachable, so the pure-logic tests still run anywhere. Network calls to yfinance are mocked, so the suite is offline and deterministic.

## What I Gained from building this

This project was built to practice core backend and database concepts: relational schema design with foreign keys, safe SQL practices (parameterized queries), API integration with error handling, and secrets management — the kind of data-handling discipline expected in production code.

Adding accounts later made the same lesson land twice. The most instructive bug was a missing `UNIQUE` constraint. Without it, buying the same ticker twice inserted a second row that the read path (`fetchone()`) could not see, while the sell path deleted by `symbol` — so selling the first lot silently destroyed the rest of the position. It was a good lesson in letting the database enforce invariants the application assumes, rather than assuming them in Python.

The multi-user version is the same lesson one level up: `UNIQUE (symbol)` had to become `UNIQUE (user_id, symbol)`, or two accounts holding the same ticker would collide — and a cash balance only stays honest if the money and the shares move in a single transaction, with the balance re-checked under a row lock rather than in Python where two requests could both pass the same check.

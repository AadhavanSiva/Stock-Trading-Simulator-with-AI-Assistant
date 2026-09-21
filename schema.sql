-- Live Portfolio Tracker — database schema
-- Usage: psql -U postgres -d practice -f schema.sql
--
-- Note on the NaN guards below: in PostgreSQL 'NaN'::numeric sorts ABOVE
-- every other numeric, so "shares > 0" alone is true for NaN. The explicit
-- <> 'NaN' term is what actually keeps NaN out of the money columns.

CREATE TABLE IF NOT EXISTS users (
    id           SERIAL PRIMARY KEY,
    -- Google's subject claim, not the email: people change their email
    -- address, but 'sub' is the stable identifier for the account.
    google_sub   TEXT UNIQUE NOT NULL,
    email        TEXT NOT NULL,
    display_name TEXT,
    cash         NUMERIC NOT NULL DEFAULT 50000
        CONSTRAINT users_cash_sane
        CHECK (cash >= 0 AND cash <> 'NaN'),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Appearing on the leaderboard is opt-in, and off until someone says
    -- otherwise. A default of TRUE would publish the standing of every
    -- account that existed before the feature did, none of which agreed
    -- to anything.
    leaderboard_opt_in BOOLEAN NOT NULL DEFAULT FALSE,
    -- The name shown there, chosen for the purpose. The leaderboard never
    -- renders `email` or `display_name`: both come from Google and are
    -- usually a real name and a real address, which nobody supplied in
    -- order to have them published next to their net worth.
    leaderboard_name TEXT
        CONSTRAINT users_leaderboard_name_present
        CHECK (leaderboard_name IS NULL OR length(btrim(leaderboard_name)) > 0),
    -- Opting in without choosing a name would leave the row with nothing
    -- safe to display, so the database refuses that combination outright
    -- rather than leaving the view to pick a fallback — and the obvious
    -- fallback is exactly the address we are trying not to publish.
    CONSTRAINT users_leaderboard_needs_a_name
        CHECK (NOT leaderboard_opt_in OR leaderboard_name IS NOT NULL)
);

-- One account per leaderboard name.
--
-- The application also checks this before saving, so it can say "someone
-- is already using that name" rather than surface a constraint error — but
-- a check followed by an update is two statements, and two people claiming
-- the same name at once would both pass it. This is what actually settles
-- it; the friendly message is a courtesy on top.
--
-- Partial, because NULL means "has not chosen one" and any number of
-- accounts may be in that state. Case- and space-insensitive, so "Racer"
-- and " racer " cannot both be taken.
CREATE UNIQUE INDEX IF NOT EXISTS users_leaderboard_name_key
    ON users (lower(btrim(leaderboard_name)))
    WHERE leaderboard_name IS NOT NULL;

CREATE TABLE IF NOT EXISTS stocks (
    symbol        TEXT PRIMARY KEY,
    company_name  TEXT,
    current_price NUMERIC
        CONSTRAINT stocks_current_price_sane
        CHECK (current_price IS NULL OR (current_price >= 0 AND current_price <> 'NaN')),
    -- The previous session's closing price, which is what "today's change"
    -- is measured from. It comes from the same quote as current_price and
    -- is stored beside it, because the two have to describe the same
    -- moment: taking the close from a later lookup than the price would
    -- report a change across a window nobody asked about.
    -- Strictly positive, unlike current_price, which is only ever
    -- displayed. This one is divided by (today's percentage) and summed
    -- into a basis, so a zero is not a usable value — NULL is how "not
    -- known" is spelled, and a zero slipping through would contribute a
    -- whole position's market value to the account's change for the day
    -- while contributing nothing to the basis it is measured against.
    previous_close NUMERIC
        CONSTRAINT stocks_previous_close_sane
        CHECK (previous_close IS NULL OR (previous_close > 0 AND previous_close <> 'NaN')),
    -- When every available day was last downloaded. NULL means only a
    -- partial window is stored, so an "all time" chart must say so rather
    -- than present six months as the whole history.
    full_history_loaded_at TIMESTAMPTZ,
    -- When current_price was last written. This is what decides whether a
    -- page triggers a background refresh, and what the "prices as of ..."
    -- line on every page reads from.
    --
    -- In the database rather than in a process, deliberately: an
    -- in-memory timestamp forgets everything on restart, so the first
    -- page view after a deploy would re-quote every symbol an account
    -- holds for no reason. It also makes staleness a fact a second
    -- process could agree with, if there is ever a second process.
    updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS portfolio (
    id             SERIAL PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol         TEXT NOT NULL REFERENCES stocks(symbol),
    shares         NUMERIC NOT NULL
        CONSTRAINT portfolio_shares_positive
        CHECK (shares > 0 AND shares <> 'NaN'),
    purchase_price NUMERIC NOT NULL
        CONSTRAINT portfolio_purchase_price_sane
        CHECK (purchase_price >= 0 AND purchase_price <> 'NaN'),
    -- One row per symbol PER USER: a repeat buy merges into a weighted-average
    -- cost basis. This must include user_id — a bare UNIQUE (symbol) would
    -- collide across accounts and hand one person another person's position.
    CONSTRAINT portfolio_user_symbol_key UNIQUE (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS portfolio_user_idx ON portfolio (user_id);

-- Prices are shared: AAPL costs the same for everyone, so price data is
-- deliberately not per-user.
CREATE TABLE IF NOT EXISTS price_history (
    id      SERIAL PRIMARY KEY,
    -- NOT NULL is load-bearing next to the UNIQUE below: PostgreSQL treats
    -- NULLs as distinct in a unique constraint, so a nullable symbol would
    -- let the same day be inserted over and over and quietly break the
    -- "safe to re-run" guarantee the loader depends on.
    symbol  TEXT NOT NULL REFERENCES stocks(symbol) ON DELETE CASCADE,
    date    DATE NOT NULL,
    -- The OHLC columns stay nullable: a feed genuinely leaves gaps, and the
    -- readers already filter them. What they must never hold is a NaN.
    -- 'NaN'::numeric sorts ABOVE every other numeric, so one bad cell makes
    -- MAX(close) return NaN and every high on the history page becomes NaN
    -- with nothing to show where it came from. The Python loader filters
    -- NaN too; this is the guarantee, that is the convenience.
    open    NUMERIC CONSTRAINT price_history_open_sane
            CHECK (open IS NULL OR (open >= 0 AND open <> 'NaN')),
    high    NUMERIC CONSTRAINT price_history_high_sane
            CHECK (high IS NULL OR (high >= 0 AND high <> 'NaN')),
    low     NUMERIC CONSTRAINT price_history_low_sane
            CHECK (low IS NULL OR (low >= 0 AND low <> 'NaN')),
    close   NUMERIC CONSTRAINT price_history_close_sane
            CHECK (close IS NULL OR (close >= 0 AND close <> 'NaN')),
    volume  BIGINT CONSTRAINT price_history_volume_sane
            CHECK (volume IS NULL OR volume >= 0),
    UNIQUE (symbol, date)
);

CREATE INDEX IF NOT EXISTS price_history_symbol_date_idx
    ON price_history (symbol, date DESC);

-- Intraday bars, for the 1-day and 5-day charts.
--
-- These cannot live in price_history: that table is keyed UNIQUE (symbol,
-- date) on a DATE, so it holds exactly one row per trading day by design.
-- Intraday needs a timestamp and many rows per day. It is also a cache to
-- be refilled rather than a record to be kept, though the reason changed
-- with the provider: Yahoo simply would not serve more than about a week
-- of 1-minute bars, whereas Alpaca serves 1-minute bars for roughly a
-- month and coarser ones for years. What bounds this table now is what
-- the 1D and 5D charts ask for, not what can be obtained — so it stays
-- small by choice, and anything deleted can be fetched again.
CREATE TABLE IF NOT EXISTS price_intraday (
    id      SERIAL PRIMARY KEY,
    symbol  TEXT NOT NULL REFERENCES stocks(symbol) ON DELETE CASCADE,
    ts      TIMESTAMPTZ NOT NULL,
    close   NUMERIC NOT NULL
        CONSTRAINT price_intraday_close_sane
        CHECK (close >= 0 AND close <> 'NaN'),
    UNIQUE (symbol, ts)
);

CREATE INDEX IF NOT EXISTS price_intraday_symbol_ts_idx
    ON price_intraday (symbol, ts DESC);

-- One row per question asked of the assistant, for its rate limit.
--
-- In the database rather than in memory so the limit survives a restart and
-- holds across every worker process. Rows older than the limit's window are
-- deleted as each new question is checked, so this stays small: at most
-- the limit's worth of rows per account.
CREATE TABLE IF NOT EXISTS assistant_requests (
    id        BIGSERIAL PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    asked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS assistant_requests_user_time_idx
    ON assistant_requests (user_id, asked_at);

-- Every buy and sell, append-only.
--
-- This is the account's ledger: the positions in `portfolio` are a running
-- total, but this is how they got that way. Each row is written inside the
-- same transaction that moves the cash and the shares (see
-- models/portfolio.py), so the log can never disagree with the balances —
-- there is no path that records a trade without moving the money, or the
-- other way round.
--
-- `cost_basis` is the weighted-average price per share at the instant of a
-- sale, captured while the holding is locked. It is what makes realized
-- gain a plain sum over this table instead of a replay of every prior buy,
-- and it is only meaningful on a sell, which the CHECK below enforces.
CREATE TABLE IF NOT EXISTS trades (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol      TEXT NOT NULL REFERENCES stocks(symbol),
    side        TEXT NOT NULL
        CONSTRAINT trades_side_known CHECK (side IN ('buy', 'sell')),
    shares      NUMERIC NOT NULL
        CONSTRAINT trades_shares_positive CHECK (shares > 0 AND shares <> 'NaN'),
    price       NUMERIC NOT NULL
        CONSTRAINT trades_price_sane CHECK (price >= 0 AND price <> 'NaN'),
    -- The cash that actually moved, in whole cents, as the transaction
    -- applied it. Stored rather than recomputed so the ledger reports the
    -- figure the balance was changed by, not a re-rounding of it.
    total_value NUMERIC NOT NULL
        CONSTRAINT trades_total_value_sane CHECK (total_value >= 0 AND total_value <> 'NaN'),
    cost_basis  NUMERIC
        CONSTRAINT trades_cost_basis_sane
        CHECK (cost_basis IS NULL OR (cost_basis >= 0 AND cost_basis <> 'NaN'))
        CONSTRAINT trades_cost_basis_only_on_sells
        CHECK ((side = 'sell') = (cost_basis IS NOT NULL)),
    -- True for the opening rows migration 006 synthesized for positions
    -- that predate this table, so the UI can say "opening position" rather
    -- than present a trade that never happened at a time it did not happen.
    backfilled  BOOLEAN NOT NULL DEFAULT FALSE,
    traded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Paging reads newest-first; id breaks ties so two trades in the same
-- instant cannot swap places between one page and the next.
CREATE INDEX IF NOT EXISTS trades_user_time_idx
    ON trades (user_id, traded_at DESC, id DESC);

-- Realized gain per position groups by symbol within one account.
CREATE INDEX IF NOT EXISTS trades_user_symbol_idx
    ON trades (user_id, symbol);

-- A ledger the application can only append to.
--
-- Immutability here is not a convention the code is trusted to keep: the
-- database refuses the UPDATE or DELETE outright, so a bug, a stray query
-- or a hand-typed psql session cannot quietly rewrite what happened. A
-- correction is a new row, never an edit to an old one.
--
-- The one sanctioned exception is erasing an account: deleting a person's
-- data has to be able to remove their trades too. That path sets
-- app.erasing_account for the length of its transaction (see
-- users.delete_account), which is deliberately awkward to do by accident
-- and shows up plainly in the code that does it.
CREATE OR REPLACE FUNCTION trades_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_setting('app.erasing_account', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'trades is append-only: % on trade id % was refused',
                    TG_OP, OLD.id
        USING ERRCODE = 'restrict_violation',
              HINT = 'Corrections are new rows, never edits. Deleting an '
                     'account is the one exception: use users.delete_account, '
                     'which sets app.erasing_account for its transaction.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trades_no_rewrite ON trades;
CREATE TRIGGER trades_no_rewrite
    BEFORE UPDATE OR DELETE ON trades
    FOR EACH ROW EXECUTE FUNCTION trades_append_only();

-- What an account was worth, sampled over time.
--
-- Cash plus the market value of the holdings, written whenever prices are
-- refreshed. This is the only record of the past: `portfolio` holds the
-- position as it stands now, and nothing else remembers what it was worth
-- last week, so a performance chart cannot be reconstructed after the fact.
-- Every sample must therefore be taken at the moment it is true.
--
-- The three figures are stored rather than just the total, so a later
-- reader can tell a gain from a deposit of attention — cash falling while
-- holdings rise is a purchase, not a loss.
--
-- It is also what the leaderboard reads. Ranking accounts by re-pricing
-- every holding on every page load would mean one market-data pass per
-- viewer; this table already holds the answer as of the last refresh.
CREATE TABLE IF NOT EXISTS portfolio_value_history (
    id             BIGSERIAL PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    cash           NUMERIC NOT NULL
        CONSTRAINT pvh_cash_sane CHECK (cash >= 0 AND cash <> 'NaN'),
    holdings_value NUMERIC NOT NULL
        CONSTRAINT pvh_holdings_value_sane
        CHECK (holdings_value >= 0 AND holdings_value <> 'NaN'),
    total_value    NUMERIC NOT NULL
        CONSTRAINT pvh_total_value_sane
        CHECK (total_value >= 0 AND total_value <> 'NaN')
);

-- Both reads this serves are "newest first, for one account": the chart
-- walks back through a window, and the leaderboard takes just the latest.
CREATE INDEX IF NOT EXISTS portfolio_value_history_user_time_idx
    ON portfolio_value_history (user_id, recorded_at DESC);

-- Tickers someone follows without owning.
--
-- Separate from `portfolio` rather than a flag on it: a holding has shares
-- and a cost basis, a watched ticker has neither, and widening `portfolio`
-- to carry zero-share rows would mean every query that reads a position
-- learning to exclude them. The two answer different questions.
CREATE TABLE IF NOT EXISTS watchlist (
    id       SERIAL PRIMARY KEY,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol   TEXT NOT NULL REFERENCES stocks(symbol) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One entry per ticker per account, so "add" is idempotent and the
    -- page cannot list the same company twice.
    CONSTRAINT watchlist_user_symbol_key UNIQUE (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS watchlist_user_idx ON watchlist (user_id);

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
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stocks (
    symbol        TEXT PRIMARY KEY,
    company_name  TEXT,
    current_price NUMERIC
        CONSTRAINT stocks_current_price_sane
        CHECK (current_price IS NULL OR (current_price >= 0 AND current_price <> 'NaN')),
    -- When every available day was last downloaded. NULL means only a
    -- partial window is stored, so an "all time" chart must say so rather
    -- than present six months as the whole history.
    full_history_loaded_at TIMESTAMPTZ
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
    symbol  TEXT REFERENCES stocks(symbol),
    date    DATE NOT NULL,
    open    NUMERIC,
    high    NUMERIC,
    low     NUMERIC,
    close   NUMERIC,
    volume  BIGINT,
    UNIQUE (symbol, date)
);

CREATE INDEX IF NOT EXISTS price_history_symbol_date_idx
    ON price_history (symbol, date DESC);

-- Intraday bars, for the 1-day and 5-day charts.
--
-- These cannot live in price_history: that table is keyed UNIQUE (symbol,
-- date) on a DATE, so it holds exactly one row per trading day by design.
-- Intraday needs a timestamp and many rows per day. It is also far more
-- perishable — Yahoo only serves about 7 days of 1-minute bars and 60 days
-- of coarser ones — so this table is a cache to be refilled, not a record
-- to be kept.
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

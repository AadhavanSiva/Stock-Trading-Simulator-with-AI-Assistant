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
        CHECK (current_price IS NULL OR (current_price >= 0 AND current_price <> 'NaN'))
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

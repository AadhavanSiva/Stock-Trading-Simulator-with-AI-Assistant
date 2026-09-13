-- Migration 003 — intraday bars for the 1-day and 5-day charts.
--
-- price_history is keyed UNIQUE (symbol, date) on a DATE column, so it can
-- hold exactly one row per trading day. Intraday data needs a timestamp and
-- many rows per day, so it gets its own table rather than a widened one.
--
-- Treat this table as a cache: Yahoo serves only about 7 days of 1-minute
-- bars and 60 days of coarser intervals, so old rows stop being refreshable
-- and can be pruned without losing anything that matters.
--
-- Safe to re-run.
-- Usage: psql -U postgres -d practice -f migrations/003_intraday_prices.sql

BEGIN;

CREATE TABLE IF NOT EXISTS price_intraday (
    id      SERIAL PRIMARY KEY,
    symbol  TEXT NOT NULL REFERENCES stocks(symbol) ON DELETE CASCADE,
    ts      TIMESTAMPTZ NOT NULL,
    close   NUMERIC NOT NULL,
    UNIQUE (symbol, ts)
);

DO $$ BEGIN
    ALTER TABLE price_intraday ADD CONSTRAINT price_intraday_close_sane
        CHECK (close >= 0 AND close <> 'NaN');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS price_intraday_symbol_ts_idx
    ON price_intraday (symbol, ts DESC);

COMMIT;

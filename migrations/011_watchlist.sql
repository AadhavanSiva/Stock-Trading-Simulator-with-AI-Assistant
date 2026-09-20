-- Migration 011: follow tickers without owning them.
--
--   psql -U postgres -d practice -f migrations/011_watchlist.sql
--
-- Nothing is backfilled: nobody has followed anything yet, and seeding
-- watchlists from existing holdings would put companies on a list nobody
-- asked to watch.
--
-- Safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS watchlist (
    id       SERIAL PRIMARY KEY,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol   TEXT NOT NULL REFERENCES stocks(symbol) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT watchlist_user_symbol_key UNIQUE (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS watchlist_user_idx ON watchlist (user_id);

COMMIT;

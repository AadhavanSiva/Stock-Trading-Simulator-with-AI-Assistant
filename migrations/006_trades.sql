-- Migration 006: the append-only trade log.
--
--   psql -U postgres -d practice -f migrations/006_trades.sql
--
-- Adds `trades`, the ledger behind every position, and gives accounts that
-- predate it one synthesized opening row per holding so their activity and
-- realized gains are not simply blank.
--
-- Safe to re-run: every step is guarded, and the backfill skips holdings
-- that already have an opening row.

BEGIN;

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
    total_value NUMERIC NOT NULL
        CONSTRAINT trades_total_value_sane CHECK (total_value >= 0 AND total_value <> 'NaN'),
    cost_basis  NUMERIC
        CONSTRAINT trades_cost_basis_sane
        CHECK (cost_basis IS NULL OR (cost_basis >= 0 AND cost_basis <> 'NaN'))
        CONSTRAINT trades_cost_basis_only_on_sells
        CHECK ((side = 'sell') = (cost_basis IS NOT NULL)),
    backfilled  BOOLEAN NOT NULL DEFAULT FALSE,
    traded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS trades_user_time_idx
    ON trades (user_id, traded_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS trades_user_symbol_idx
    ON trades (user_id, symbol);

-- One opening row per existing holding, at the weighted-average cost the
-- position already carries.
--
-- These are marked backfilled because they are a reconstruction, not a
-- record: the real purchases happened at prices and times nobody wrote
-- down. Dating them from the account's creation keeps the ledger in
-- chronological order without inventing a more precise moment than we
-- actually know. The UI reads the flag and labels them "opening position".
--
-- This runs before the trigger below is installed, so the insert is a
-- plain insert; re-running it after the trigger exists is still fine,
-- since appending is exactly what the trigger permits.
INSERT INTO trades (user_id, symbol, side, shares, price, total_value, backfilled, traded_at)
SELECT p.user_id,
       p.symbol,
       'buy',
       p.shares,
       p.purchase_price,
       round(p.shares * p.purchase_price, 2),
       TRUE,
       u.created_at
FROM portfolio p
JOIN users u ON u.id = p.user_id
WHERE NOT EXISTS (
    SELECT 1 FROM trades t
    WHERE t.user_id = p.user_id
      AND t.symbol = p.symbol
      AND t.backfilled
);

-- The log is append-only, enforced here rather than trusted to the
-- application. See the matching comment in schema.sql for why erasing an
-- account is the one permitted exception and how it announces itself.
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

COMMIT;

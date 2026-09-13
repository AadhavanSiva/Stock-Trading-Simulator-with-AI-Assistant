-- Migration 001 — collapse duplicate lots and enforce one row per symbol.
--
-- Before this, portfolio.symbol had no UNIQUE constraint. A repeat buy
-- inserted a second lot that get_holding()/reduce_holding() could not see
-- (both use fetchone()), while a full sale ran
--     DELETE FROM portfolio WHERE symbol = %s
-- and removed EVERY lot for that symbol. Selling the first lot therefore
-- destroyed the rest of the position.
--
-- Safe to re-run: each step is guarded.
-- Usage: psql -U postgres -d practice -f migrations/001_one_row_per_symbol.sql

BEGIN;

-- 1. Drop holdings that can never be valid, so the CHECKs below can apply.
DELETE FROM portfolio
WHERE shares IS NULL
   OR purchase_price IS NULL
   OR shares <= 0
   OR shares = 'NaN'
   OR purchase_price = 'NaN';

-- 2. Merge duplicate lots into the lowest id, at a weighted-average cost.
WITH merged AS (
    SELECT symbol,
           SUM(shares) AS total_shares,
           SUM(shares * purchase_price) / SUM(shares) AS avg_cost,
           MIN(id) AS keep_id
    FROM portfolio
    GROUP BY symbol
    HAVING COUNT(*) > 1
)
UPDATE portfolio p
SET shares = m.total_shares,
    purchase_price = m.avg_cost
FROM merged m
WHERE p.id = m.keep_id;

-- 3. Remove the now-redundant duplicate rows.
DELETE FROM portfolio p
WHERE p.id <> (SELECT MIN(id) FROM portfolio q WHERE q.symbol = p.symbol);

-- 4. Enforce the invariant from here on.
ALTER TABLE portfolio ALTER COLUMN symbol SET NOT NULL;
ALTER TABLE portfolio ALTER COLUMN shares SET NOT NULL;
ALTER TABLE portfolio ALTER COLUMN purchase_price SET NOT NULL;

DO $$ BEGIN
    ALTER TABLE portfolio ADD CONSTRAINT portfolio_symbol_key UNIQUE (symbol);
EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE portfolio ADD CONSTRAINT portfolio_shares_positive
        CHECK (shares > 0 AND shares <> 'NaN');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE portfolio ADD CONSTRAINT portfolio_purchase_price_sane
        CHECK (purchase_price >= 0 AND purchase_price <> 'NaN');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE stocks ADD CONSTRAINT stocks_current_price_sane
        CHECK (current_price IS NULL OR (current_price >= 0 AND current_price <> 'NaN'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS price_history_symbol_date_idx
    ON price_history (symbol, date DESC);

COMMIT;

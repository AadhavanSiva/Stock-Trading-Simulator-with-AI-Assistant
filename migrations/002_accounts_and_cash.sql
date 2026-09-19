-- Migration 002 — user accounts, per-user portfolios, and a cash balance.
--
-- Before this the app was single-user: `portfolio` held *the* portfolio,
-- with UNIQUE (symbol). Adding accounts means every holding needs an owner,
-- and the uniqueness rule has to become per-user. A bare UNIQUE (symbol)
-- alongside multiple accounts would let one user's row satisfy another
-- user's lookup — the same class of bug as migration 001, but leaking
-- across accounts instead of losing shares.
--
-- Any holdings that exist when this runs are adopted by a placeholder
-- account so nothing is orphaned or dropped.
--
-- Safe to re-run.
-- Usage: psql -U postgres -d practice -f migrations/002_accounts_and_cash.sql

BEGIN;

CREATE TABLE IF NOT EXISTS users (
    id           SERIAL PRIMARY KEY,
    google_sub   TEXT UNIQUE NOT NULL,
    email        TEXT NOT NULL,
    display_name TEXT,
    cash         NUMERIC NOT NULL DEFAULT 50000,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT users_cash_sane
        CHECK (cash >= 0 AND cash <> 'NaN');
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

-- 1. Give portfolio an owner column.
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS user_id INTEGER;

-- 2. Adopt any pre-existing holdings into a placeholder account, so the
--    NOT NULL below can be applied without deleting anyone's data. Signing
--    in with Google later creates a separate, real account; this one can be
--    reassigned or removed once you know which is which.
INSERT INTO users (google_sub, email, display_name)
SELECT 'legacy-single-user', 'legacy@localhost', 'Portfolio (before accounts)'
WHERE EXISTS (SELECT 1 FROM portfolio WHERE user_id IS NULL)
  AND NOT EXISTS (SELECT 1 FROM users WHERE google_sub = 'legacy-single-user');

UPDATE portfolio
SET user_id = (SELECT id FROM users WHERE google_sub = 'legacy-single-user')
WHERE user_id IS NULL;

-- 3. Now the column can be made mandatory and wired to users.
ALTER TABLE portfolio ALTER COLUMN user_id SET NOT NULL;

DO $$ BEGIN
    ALTER TABLE portfolio ADD CONSTRAINT portfolio_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

-- 4. Swap the single-user uniqueness rule for the per-user one.
ALTER TABLE portfolio DROP CONSTRAINT IF EXISTS portfolio_symbol_key;

DO $$ BEGIN
    ALTER TABLE portfolio ADD CONSTRAINT portfolio_user_symbol_key
        UNIQUE (user_id, symbol);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS portfolio_user_idx ON portfolio (user_id);

COMMIT;

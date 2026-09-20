-- Migration 009: store the previous close, for "today's change".
--
--   psql -U postgres -d practice -f migrations/009_previous_close.sql
--
-- Nothing is backfilled. The previous close is a fact about a particular
-- session, and the app has no record of which session any stored price
-- came from — inventing one would put a number on screen that looks like
-- today's change and is not. The column fills in on the next refresh, and
-- until it does the UI says so rather than showing a zero.
--
-- Safe to re-run.

BEGIN;

ALTER TABLE stocks ADD COLUMN IF NOT EXISTS previous_close NUMERIC;

ALTER TABLE stocks DROP CONSTRAINT IF EXISTS stocks_previous_close_sane;
ALTER TABLE stocks
    ADD CONSTRAINT stocks_previous_close_sane
    CHECK (previous_close IS NULL OR (previous_close >= 0 AND previous_close <> 'NaN'));

COMMIT;

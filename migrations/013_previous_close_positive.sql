-- Migration 013: a previous close of zero is not a price.
--
--   psql -U postgres -d practice -f migrations/013_previous_close_positive.sql
--
-- previous_close is divided by to get today's percentage and summed into
-- the basis that percentage is measured against, so zero is not a usable
-- value. NULL is how "not known" is spelled, and every reader already
-- handles it. A zero slipping through would contribute a whole position's
-- market value to the day's change while contributing nothing to its
-- basis, quietly inflating the account total.
--
-- current_price keeps its >= 0 constraint: it is only ever displayed.
--
-- Safe to re-run.

BEGIN;

UPDATE stocks SET previous_close = NULL WHERE previous_close = 0;

ALTER TABLE stocks DROP CONSTRAINT IF EXISTS stocks_previous_close_sane;
ALTER TABLE stocks
    ADD CONSTRAINT stocks_previous_close_sane
    CHECK (previous_close IS NULL OR (previous_close > 0 AND previous_close <> 'NaN'));

COMMIT;

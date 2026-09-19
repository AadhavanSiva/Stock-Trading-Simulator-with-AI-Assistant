-- Migration 007: give price_history the guarantees every other table has.
--
--   psql -U postgres -d practice -f migrations/007_price_history_integrity.sql
--
-- `price_history` was the one money-bearing table with no CHECK constraints
-- and a nullable symbol, which made the README's blanket claim about NaN
-- guards untrue for it. The newer `price_intraday` had them from the start;
-- this brings the older table into line.
--
-- Existing bad rows are repaired before the constraints go on, so the
-- migration cannot fail halfway on data that predates it.
--
-- Safe to re-run: the repairs match nothing the second time, and each
-- constraint is dropped before it is re-added.

BEGIN;

-- 1. Rows with no symbol cannot be attributed to anything and are not
--    reachable by any query the app makes (every read is keyed on symbol).
--    They also defeat UNIQUE (symbol, date), since NULLs compare distinct.
DELETE FROM price_history WHERE symbol IS NULL;

-- 2. Any row whose price is NaN or negative. NULL is the honest value for a
--    gap — the loaders already produce NULL for a missing cell, and every
--    reader filters on IS NOT NULL — so a nonsense figure becomes one
--    rather than being deleted, which would lose the trading day entirely.
UPDATE price_history SET open   = NULL WHERE open   IS NOT NULL AND (open   = 'NaN' OR open   < 0);
UPDATE price_history SET high   = NULL WHERE high   IS NOT NULL AND (high   = 'NaN' OR high   < 0);
UPDATE price_history SET low    = NULL WHERE low    IS NOT NULL AND (low    = 'NaN' OR low    < 0);
UPDATE price_history SET close  = NULL WHERE close  IS NOT NULL AND (close  = 'NaN' OR close  < 0);
UPDATE price_history SET volume = NULL WHERE volume IS NOT NULL AND volume < 0;

-- 3. Now the constraints can go on without tripping over history.
ALTER TABLE price_history ALTER COLUMN symbol SET NOT NULL;

ALTER TABLE price_history DROP CONSTRAINT IF EXISTS price_history_open_sane;
ALTER TABLE price_history DROP CONSTRAINT IF EXISTS price_history_high_sane;
ALTER TABLE price_history DROP CONSTRAINT IF EXISTS price_history_low_sane;
ALTER TABLE price_history DROP CONSTRAINT IF EXISTS price_history_close_sane;
ALTER TABLE price_history DROP CONSTRAINT IF EXISTS price_history_volume_sane;

ALTER TABLE price_history
    ADD CONSTRAINT price_history_open_sane
    CHECK (open IS NULL OR (open >= 0 AND open <> 'NaN')),
    ADD CONSTRAINT price_history_high_sane
    CHECK (high IS NULL OR (high >= 0 AND high <> 'NaN')),
    ADD CONSTRAINT price_history_low_sane
    CHECK (low IS NULL OR (low >= 0 AND low <> 'NaN')),
    ADD CONSTRAINT price_history_close_sane
    CHECK (close IS NULL OR (close >= 0 AND close <> 'NaN')),
    ADD CONSTRAINT price_history_volume_sane
    CHECK (volume IS NULL OR volume >= 0);

-- 4. Match price_intraday, which cascades. The two tables held the same
--    kind of data under different rules: deleting a stock would cascade one
--    and fail on the other, so neither outcome was the designed one.
ALTER TABLE price_history DROP CONSTRAINT IF EXISTS price_history_symbol_fkey;
ALTER TABLE price_history
    ADD CONSTRAINT price_history_symbol_fkey
    FOREIGN KEY (symbol) REFERENCES stocks(symbol) ON DELETE CASCADE;

COMMIT;

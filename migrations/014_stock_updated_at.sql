-- Migration 014: when a price was last written.
--
--   psql -U postgres -d practice -f migrations/014_stock_updated_at.sql
--
-- Drives the automatic background refresh and the "prices as of ..." line
-- the pages now show.
--
-- Existing rows are left NULL rather than stamped with now(). A NULL here
-- means "we do not know when this was fetched", which is the truth;
-- stamping them would claim every stored price was refreshed at migration
-- time and suppress the first refresh for five minutes on a set of prices
-- that might be weeks old. NULL sorts as stale, so the first page view
-- refreshes them and records an honest timestamp.
--
-- Safe to re-run.

BEGIN;

ALTER TABLE stocks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

COMMIT;

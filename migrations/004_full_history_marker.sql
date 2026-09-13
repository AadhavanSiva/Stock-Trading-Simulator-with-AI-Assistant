-- Migration 004 — remember when a stock's full price history was loaded.
--
-- Without this, the "All" chart could not tell complete history from a
-- partial window. A stock loaded with the 6-month "Load past prices"
-- action drew six months under an "All time" label, with no prompt to
-- fetch the rest, because the only check was "fewer than 2 points".
--
-- Existing rows start NULL (not known to be complete), so the chart offers
-- to load full history once rather than wrongly assuming it is there.
--
-- Safe to re-run.
-- Usage: psql -U postgres -d practice -f migrations/004_full_history_marker.sql

ALTER TABLE stocks ADD COLUMN IF NOT EXISTS full_history_loaded_at TIMESTAMPTZ;

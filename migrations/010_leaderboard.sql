-- Migration 010: opt-in leaderboard participation.
--
--   psql -U postgres -d practice -f migrations/010_leaderboard.sql
--
-- Nobody is opted in by this migration. Publishing the standing of every
-- existing account would be publishing data those people never agreed to
-- share, and a default of TRUE would do exactly that on the next deploy.
--
-- Safe to re-run.

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS
    leaderboard_opt_in BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS leaderboard_name TEXT;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_leaderboard_name_present;
ALTER TABLE users
    ADD CONSTRAINT users_leaderboard_name_present
    CHECK (leaderboard_name IS NULL OR length(btrim(leaderboard_name)) > 0);

-- An opted-in row with no name has nothing safe to display, and the
-- obvious fallback would be the email address this feature exists to keep
-- off the page. The pairing is enforced rather than left to the view.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_leaderboard_needs_a_name;
ALTER TABLE users
    ADD CONSTRAINT users_leaderboard_needs_a_name
    CHECK (NOT leaderboard_opt_in OR leaderboard_name IS NOT NULL);

COMMIT;

-- Migration 012: one account per leaderboard name.
--
--   psql -U postgres -d practice -f migrations/012_leaderboard_name_unique.sql
--
-- The application checked for a clash before saving, which is two
-- statements: two people claiming the same name at once both passed it.
-- The check stays, so the message is still a readable one, but the
-- database is what settles the race.
--
-- Safe to re-run.

BEGIN;

-- Any existing clash is resolved before the index goes on, keeping the
-- oldest account's name and suffixing the others with their id. The name
-- is not cleared: an opted-in row with no name violates
-- users_leaderboard_needs_a_name, so clearing it would trade one broken
-- invariant for another.
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY lower(btrim(leaderboard_name)) ORDER BY id
           ) AS position
    FROM users
    WHERE leaderboard_name IS NOT NULL
)
UPDATE users u
SET leaderboard_name = u.leaderboard_name || '-' || u.id
FROM ranked
WHERE ranked.id = u.id AND ranked.position > 1;

CREATE UNIQUE INDEX IF NOT EXISTS users_leaderboard_name_key
    ON users (lower(btrim(leaderboard_name)))
    WHERE leaderboard_name IS NOT NULL;

COMMIT;

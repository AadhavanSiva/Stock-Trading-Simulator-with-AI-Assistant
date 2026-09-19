-- Migration 005 — keep the assistant's rate limit in the database.
--
-- The limit (questions per account per window) used to be counted in the
-- web process's memory. That reset on every restart and was per process,
-- so running two workers doubled the allowance. Counting rows here makes
-- one limit that every process shares.
--
-- Safe to re-run.
-- Usage: psql -U postgres -d practice -f migrations/005_assistant_rate_limit.sql

BEGIN;

CREATE TABLE IF NOT EXISTS assistant_requests (
    id        BIGSERIAL PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    asked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS assistant_requests_user_time_idx
    ON assistant_requests (user_id, asked_at);

COMMIT;

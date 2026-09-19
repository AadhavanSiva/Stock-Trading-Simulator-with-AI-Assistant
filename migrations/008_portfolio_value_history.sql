-- Migration 008: remember what an account was worth.
--
--   psql -U postgres -d practice -f migrations/008_portfolio_value_history.sql
--
-- Adds portfolio_value_history, sampled whenever prices refresh, and seeds
-- one opening sample per existing account so a chart has somewhere to start
-- rather than appearing empty until the first refresh.
--
-- Safe to re-run: the table is guarded, and the seed skips accounts that
-- already have a sample.

BEGIN;

CREATE TABLE IF NOT EXISTS portfolio_value_history (
    id             BIGSERIAL PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    cash           NUMERIC NOT NULL
        CONSTRAINT pvh_cash_sane CHECK (cash >= 0 AND cash <> 'NaN'),
    holdings_value NUMERIC NOT NULL
        CONSTRAINT pvh_holdings_value_sane
        CHECK (holdings_value >= 0 AND holdings_value <> 'NaN'),
    total_value    NUMERIC NOT NULL
        CONSTRAINT pvh_total_value_sane
        CHECK (total_value >= 0 AND total_value <> 'NaN')
);

CREATE INDEX IF NOT EXISTS portfolio_value_history_user_time_idx
    ON portfolio_value_history (user_id, recorded_at DESC);

-- One opening sample per account that has none.
--
-- Priced from whatever `stocks.current_price` holds now, which is the only
-- valuation available — the past was never recorded. A holding with no
-- stored price contributes nothing rather than making the whole sample
-- NULL, matching how account_summary leaves unpriced rows out of its
-- totals. Dated from the account's creation, like migration 006's opening
-- trades, so the series begins where the account does.
INSERT INTO portfolio_value_history (user_id, recorded_at, cash, holdings_value, total_value)
SELECT u.id,
       u.created_at,
       u.cash,
       coalesce(held.value, 0),
       u.cash + coalesce(held.value, 0)
FROM users u
LEFT JOIN (
    SELECT p.user_id, sum(p.shares * s.current_price) AS value
    FROM portfolio p
    JOIN stocks s ON s.symbol = p.symbol
    WHERE s.current_price IS NOT NULL
    GROUP BY p.user_id
) held ON held.user_id = u.id
WHERE NOT EXISTS (
    SELECT 1 FROM portfolio_value_history h WHERE h.user_id = u.id
);

COMMIT;

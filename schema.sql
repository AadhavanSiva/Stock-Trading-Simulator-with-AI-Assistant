-- Live Portfolio Tracker — database schema
-- Usage: psql -U postgres -d practice -f schema.sql

CREATE TABLE IF NOT EXISTS stocks (
    symbol        TEXT PRIMARY KEY,
    company_name  TEXT,
    current_price NUMERIC
);

CREATE TABLE IF NOT EXISTS portfolio (
    id             SERIAL PRIMARY KEY,
    symbol         TEXT REFERENCES stocks(symbol),
    shares         NUMERIC,
    purchase_price NUMERIC
);

CREATE TABLE IF NOT EXISTS price_history (
    id      SERIAL PRIMARY KEY,
    symbol  TEXT REFERENCES stocks(symbol),
    date    DATE NOT NULL,
    open    NUMERIC,
    high    NUMERIC,
    low     NUMERIC,
    close   NUMERIC,
    volume  BIGINT,
    UNIQUE (symbol, date)
);

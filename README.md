# Live-portfolio-tracker
# Live Portfolio Tracker

A Python + PostgreSQL application that tracks a stock portfolio using real-time market data. Built as a hands-on project to practice relational database design, API integration, and secure data handling.

## Features

- Pulls live stock prices from Yahoo Finance via the `yfinance` API
- Stores portfolio holdings and stock data in a PostgreSQL database with proper relational structure (foreign keys linking holdings to stock reference data)
- Calculates real-time gain/loss per holding
- Secure credential handling using environment variables (`.env`, git-ignored)
- Defensive error handling — a failed API call or missing data for one stock doesn't crash the whole run
- Transactional database updates with rollback on failure, ensuring the database is never left in a partially-updated state
- All SQL queries use parameterized statements to prevent SQL injection

## Tech Stack

- **Python** — application logic, API integration
- **PostgreSQL** — relational data storage
- **psycopg2** — Python/PostgreSQL database driver
- **yfinance** — live market data
- **python-dotenv** — environment variable management

## Database Schema

- `stocks` — reference data per ticker (symbol, company name, current price)
- `portfolio` — holdings (symbol, shares owned, purchase price), linked to `stocks` via foreign key

## Setup

1. Clone the repo
2. Install dependencies:
3. Create a `.env` file in the project root:
4. Set up PostgreSQL with `stocks` and `portfolio` tables (see schema above)
5. Run the tracker:

## What I Gained from building this

This project was built to practice core backend and database concepts: relational schema design with foreign keys, safe SQL practices (parameterized queries), API integration with error handling, and secrets management — the kind of data-handling discipline expected in production code.


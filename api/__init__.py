"""JSON API for the portfolio tracker, served by FastAPI.

A second front door onto the same domain layer the Flask app and the CLI
use: routes here parse a request, call portfolio_tracker.operations, and
return the result as JSON. No SQL and no market-data calls live here.

Run it with:  uvicorn api.main:app --reload --port 8000
"""

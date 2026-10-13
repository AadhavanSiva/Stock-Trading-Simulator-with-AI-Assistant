"""The FastAPI application.

Run it with:  uvicorn api.main:app --reload --port 8000

Handlers are plain `def`, not `async def`: the domain layer is synchronous
psycopg2 and requests, and FastAPI runs sync handlers in a thread pool so
one slow market-data call does not stall every other request.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import settings, spa
from api.routers import (
    account, actions, assistant, auth, portfolio, stocks, trading, watchlist,
)
from portfolio_tracker.errors import MarketDataUnavailable, ValidationError

log = logging.getLogger(__name__)

# Fail at startup, not on the first sign-in, when production has no key.
settings.jwt_secret_key()

app = FastAPI(
    title="Portfolio Tracker API",
    version="1.0.0",
    # Interactive docs locally; a production API has no need to publish
    # its own map.
    docs_url=None if settings.IS_PRODUCTION else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.IS_PRODUCTION else "/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # Bearer tokens, not cookies: nothing here needs the browser to send
    # credentials on its own, so it is never allowed to.
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # Account data must not be kept by a shared cache or the back button.
    response.headers.setdefault("Cache-Control", "no-store")
    if settings.IS_PRODUCTION:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# ------------------------------------------------------------------ errors
# Every error body has the same shape, {"detail": "..."}, with a message
# written for a person. Internal error text never reaches a response.

@app.exception_handler(ValidationError)
async def validation_error(request: Request, exc: ValidationError):
    """Input a person can correct: a bad ticker, too many shares, no cash."""
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(MarketDataUnavailable)
async def market_data_unavailable(request: Request, exc: MarketDataUnavailable):
    # Already logged by market_data, with the real error.
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(RequestValidationError)
async def malformed_request(request: Request, exc: RequestValidationError):
    """A body or query the schema rejects. Says which field, briefly."""
    fields = sorted({".".join(str(part) for part in err["loc"][1:]) or "request"
                     for err in exc.errors()})
    return JSONResponse(
        {"detail": f"Check the request: {', '.join(fields)}.", "fields": fields},
        status_code=422,
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    log.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        {"detail": "Something went wrong on our side. The error has been logged. "
                   "Try again in a minute."},
        status_code=500,
    )


# ------------------------------------------------------------------ routes

@app.get("/api/healthz", tags=["health"])
def healthz():
    """Liveness only: no database, no market data, no auth."""
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(portfolio.router)
app.include_router(trading.router)
app.include_router(stocks.router)
app.include_router(watchlist.router)
app.include_router(account.router)
app.include_router(actions.router)
app.include_router(assistant.router)

# Last, because it is a catch-all: the built Angular app, when there is one.
spa.mount_spa(app)

"""Companies followed without being owned."""
from fastapi import APIRouter, Depends

from api import schemas
from api.freshness import freshen
from api.security import CurrentUser, current_user
from portfolio_tracker import operations

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=schemas.WatchlistOut)
def get_watchlist(user: CurrentUser = Depends(current_user)):
    rows = operations.watchlist_rows(user.id)
    return schemas.WatchlistOut(
        rows=[schemas.WatchRow(**row._asdict()) for row in rows],
        prices_as_of=freshen(user.id, (row.symbol for row in rows)),
    )


@router.post("", response_model=schemas.WatchChange)
def follow(body: schemas.WatchRequest, user: CurrentUser = Depends(current_user)):
    """Follow a ticker. It is priced first, so a typo never reaches the list."""
    symbol, added = operations.watch(user.id, body.symbol)
    return schemas.WatchChange(
        symbol=symbol, changed=added,
        message=(f"{symbol} added to your watchlist." if added
                 else f"{symbol} is already on your watchlist."),
    )


# Declared before /{symbol} so "refresh" is never read as a ticker.
@router.post("/refresh", response_model=schemas.WatchlistRefresh)
def refresh(user: CurrentUser = Depends(current_user)):
    """Re-quote the watched tickers.

    Separate from the holdings refresh: a watched ticker is not part of
    the account's value, so quoting one must not move the performance
    chart or the leaderboard standing.
    """
    report = operations.refresh_watchlist(user.id)
    if not report.total:
        message = "Nothing on your watchlist yet."
    else:
        message = f"Refreshed {report.updated} of {report.total}."
        if report.failed:
            message += f" {report.failed} could not be updated."
    return schemas.WatchlistRefresh(
        updated=report.updated, failed=report.failed, total=report.total, message=message)


@router.delete("/{symbol}", response_model=schemas.WatchChange)
def unfollow(symbol: str, user: CurrentUser = Depends(current_user)):
    """Stop following a ticker. Removing one that is not there is not an error."""
    symbol = symbol.strip().upper()
    removed = operations.unwatch(user.id, symbol)
    return schemas.WatchChange(
        symbol=symbol, changed=removed,
        message=(f"{symbol} removed from your watchlist." if removed
                 else f"{symbol} was not on your watchlist."),
    )

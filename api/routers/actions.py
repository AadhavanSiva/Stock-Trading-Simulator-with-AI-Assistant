"""Fetching prices on request. Neither action buys or sells anything."""
from fastapi import APIRouter, Depends

from api import schemas
from api.security import CurrentUser, current_user
from portfolio_tracker import operations

router = APIRouter(prefix="/api/actions", tags=["actions"])


def _report(title, message, kind, report):
    return schemas.ActionReport(
        title=title, message=message, kind=kind, total=report.total,
        outcomes=[schemas.SymbolOutcome(symbol=o.symbol, ok=o.ok, message=o.message)
                  for o in report.outcomes],
    )


@router.post("/refresh-prices", response_model=schemas.ActionReport)
def refresh_prices(user: CurrentUser = Depends(current_user)):
    """Re-quote everything held, and record what the account is now worth."""
    report = operations.refresh_prices(user.id)
    if not report.total:
        return _report("Price refresh",
                       "You have no holdings yet, so there were no prices to refresh.",
                       "notice", report)
    message = (f"Refreshed {report.updated} of {report.total} "
               f"{'price' if report.total == 1 else 'prices'}.")
    if report.failed:
        message += f" {report.failed} could not be updated."
    return _report("Price refresh", message,
                   "error" if report.failed and not report.updated else "success", report)


@router.post("/load-history", response_model=schemas.ActionReport)
def load_history(user: CurrentUser = Depends(current_user)):
    """Download six months of daily prices for each holding."""
    report = operations.load_all_history(user.id)
    if not report.total:
        return _report("Historical data load",
                       "You have no holdings yet, so there was no history to load.",
                       "notice", report)
    failed = sum(1 for outcome in report.outcomes if not outcome.ok)
    message = (f"Checked {report.total} {'holding' if report.total == 1 else 'holdings'} "
               f"and stored {report.new_rows} new "
               f"{'day' if report.new_rows == 1 else 'days'} of prices.")
    if failed:
        # An outage must read as one, not as "nothing new to store".
        message += (f" {failed} could not be downloaded, so "
                    f"{'it was' if failed == 1 else 'they were'} not checked.")
    kind = "error" if failed == report.total else ("notice" if failed else "success")
    return _report("Historical data load", message, kind, report)

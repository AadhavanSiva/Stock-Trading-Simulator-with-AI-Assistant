"""The signed-in account's holdings, cash and performance."""
from fastapi import APIRouter, Depends, Query

from api import schemas
from api.freshness import freshen
from api.security import CurrentUser, current_user
from portfolio_tracker import operations
from portfolio_tracker.models import history, portfolio

router = APIRouter(prefix="/api", tags=["portfolio"])


@router.get("/portfolio", response_model=schemas.PortfolioOut)
def get_portfolio(user: CurrentUser = Depends(current_user)):
    summary = operations.account_summary(user.id)
    return schemas.portfolio_out(
        summary,
        performance=operations.performance(user.id),
        total_return=operations.percent_return(user.id, summary=summary),
        prices_as_of=freshen(user.id, (row["symbol"] for row in summary.rows)),
    )


@router.get("/trades", response_model=schemas.TradePageOut)
def get_trades(page: int = Query(1, ge=1), user: CurrentUser = Depends(current_user)):
    """The full trade history a page at a time. A page past the end
    answers with the last real page, and says which one that was."""
    return schemas.trade_page_out(
        operations.trade_history(user.id, page=page),
        operations.realized_summary(user.id),
    )


@router.get("/history", response_model=schemas.HistoryOut)
def get_history(user: CurrentUser = Depends(current_user)):
    """Each holding's price now against its recent closes."""
    # One window for both, so the average and the range beside it can
    # never describe different periods.
    days = history.RECENT_DAYS
    averages = dict(history.get_recent_averages(days))
    highs_lows = {symbol: (high, low)
                  for symbol, high, low in history.get_high_low(days)}

    rows = []
    for (symbol, name, _held, _paid, current, _gain,
         _previous_close) in portfolio.get_holdings_with_details(user.id):
        high, low = highs_lows.get(symbol, (None, None))
        rows.append(schemas.PriceRange(
            symbol=symbol, company_name=name or symbol, current_price=current,
            average=averages.get(symbol), high=high, low=low,
        ))
    return schemas.HistoryOut(days=days, rows=rows)

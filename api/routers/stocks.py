"""One company: its quote, a price chart, and loading missing history."""
from fastapi import APIRouter, Depends, Query

from api import schemas
from api.freshness import freshen
from api.security import CurrentUser, current_user
from portfolio_tracker import charts, operations
from portfolio_tracker.models import watchlist
from portfolio_tracker.services import market_data

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/{symbol}", response_model=schemas.StockDetail)
def stock_detail(symbol: str, range: str | None = Query(None, max_length=8),
                 user: CurrentUser = Depends(current_user)):
    quote = operations.look_up(user.id, symbol)
    priced = operations.price_chart(quote.symbol, range)
    chart = priced.chart
    return schemas.StockDetail(
        symbol=quote.symbol,
        quote=schemas.quote_out(quote),
        watching=watchlist.contains(user.id, quote.symbol),
        prices_as_of=freshen(user.id, [quote.symbol]),
        # "All time" means the provider's history, not the company's life.
        earliest_available=market_data.EARLIEST_AVAILABLE,
        range=priced.window,
        range_label=priced.spec["label"],
        ranges=[schemas.RangeOption(key=key, label=spec["label"])
                for key, spec in charts.RANGES.items()],
        intraday=priced.spec["intraday"],
        chart=schemas.chart_out(chart),
        needs_fetch=priced.needs_fetch,
        partial=priced.partial,
        stored_since=priced.stored_since,
        # From the plotted window itself, not all stored history.
        stats=schemas.PriceStats(
            low=chart.low if chart else None,
            high=chart.high if chart else None,
            average=chart.average if chart else None,
        ),
    )


@router.post("/{symbol}/fetch", response_model=schemas.ChartFetchOut)
def fetch_chart(symbol: str, body: schemas.ChartFetchRequest,
                user: CurrentUser = Depends(current_user)):
    """Load the prices behind an empty or partial chart."""
    fetched = operations.fetch_chart_data(user.id, symbol, body.range)
    return schemas.ChartFetchOut(
        symbol=fetched.symbol,
        range=fetched.window,
        added=fetched.added,
        message=f"Fetched {fetched.added} new {fetched.noun} for {fetched.symbol}.",
    )

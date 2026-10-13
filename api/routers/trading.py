"""Quotes, search, and the buy and sell flows.

Each trade has a preview and a commit. The preview prices the order and
checks it without writing anything; the commit re-prices it from scratch,
so a preview's figures can never be replayed or edited into a trade.
"""
from fastapi import APIRouter, Depends, Query

from api import schemas
from api.security import CurrentUser, current_user
from portfolio_tracker import operations
from portfolio_tracker.errors import InsufficientFunds, ValidationError
from portfolio_tracker.models import portfolio

router = APIRouter(prefix="/api", tags=["trading"])


def _plural(shares):
    return "share" if shares == 1 else "shares"


@router.get("/search", response_model=list[schemas.SearchResult])
def search(q: str = Query("", max_length=64), limit: int = Query(8, ge=1, le=25),
           user: CurrentUser = Depends(current_user)):
    """Companies matching a ticker or name, best first.

    Reads the cached asset catalogue, so it costs no market-data request
    and does not touch the database.
    """
    return operations.search_symbols(q, limit=limit)


@router.get("/quote", response_model=schemas.QuoteOut)
def quote(symbol: str = Query(..., max_length=16), user: CurrentUser = Depends(current_user)):
    return schemas.quote_out(operations.look_up(user.id, symbol))


# -------------------------------------------------------------------- buy

@router.post("/buy/preview", response_model=schemas.BuyPreview)
def preview_buy(body: schemas.TradeRequest, user: CurrentUser = Depends(current_user)):
    quote = operations.look_up(user.id, body.symbol)
    shares = operations.parse_quantity(body.raw_shares())
    cost = operations.to_money(shares * quote.price)
    if cost > quote.cash:
        raise InsufficientFunds(cost, quote.cash)
    return schemas.BuyPreview(
        quote=schemas.quote_out(quote), shares=shares, cost=cost,
        cash_after=quote.cash - cost,
    )


@router.post("/buy", response_model=schemas.PurchaseOut)
def buy(body: schemas.TradeRequest, user: CurrentUser = Depends(current_user)):
    """The only buy route that writes. Prices the order afresh."""
    purchase = operations.buy(user.id, body.symbol, body.raw_shares())
    message = (
        f"Bought {operations.format_shares(purchase.shares)} "
        f"{_plural(purchase.shares)} of {purchase.symbol} "
        f"at ${purchase.price:,.2f} each. Cash left: ${purchase.cash:,.2f}."
    )
    return schemas.PurchaseOut(
        **purchase._asdict() | {"company_name": purchase.company_name or purchase.symbol},
        message=message,
    )


# ------------------------------------------------------------------- sell

def _holding(user_id, symbol):
    symbol = (symbol or "").strip().upper()
    held = portfolio.get_holding(user_id, symbol)
    if held is None:
        raise ValidationError(f"You do not own any {symbol}.")
    return symbol, held


@router.get("/sell/{symbol}", response_model=schemas.SellQuote)
def sell_quote(symbol: str, user: CurrentUser = Depends(current_user)):
    symbol, (owned, average_cost) = _holding(user.id, symbol)
    quote = operations.look_up(user.id, symbol)
    return schemas.SellQuote(
        symbol=symbol,
        company_name=quote.company_name or symbol,
        owned=owned,
        maximum=operations.sellable_maximum(owned),
        average_cost=average_cost,
        price=quote.price,
        cash=quote.cash,
    )


@router.post("/sell/preview", response_model=schemas.SellPreview)
def preview_sell(body: schemas.TradeRequest, user: CurrentUser = Depends(current_user)):
    symbol, (owned, average_cost) = _holding(user.id, body.symbol)
    quote = operations.look_up(user.id, symbol)
    shares = operations.parse_quantity(body.raw_shares(), maximum=owned)
    proceeds = operations.to_money(shares * quote.price)
    return schemas.SellPreview(
        symbol=symbol,
        company_name=quote.company_name or symbol,
        shares=shares,
        price=quote.price,
        proceeds=proceeds,
        gain=operations.to_money((quote.price - average_cost) * shares),
        closes=shares == owned,
        cash_after=quote.cash + proceeds,
    )


@router.post("/sell", response_model=schemas.SaleOut)
def sell(body: schemas.TradeRequest, user: CurrentUser = Depends(current_user)):
    """The only sell route that writes. Prices the sale afresh."""
    sale = operations.sell(user.id, body.symbol, body.raw_shares())
    message = (
        f"Sold {operations.format_shares(sale.shares)} {_plural(sale.shares)} "
        f"of {sale.symbol} for ${sale.proceeds:,.2f}."
    )
    message += (
        f" Your {sale.symbol} position is now closed." if sale.closed
        else f" You still own {operations.format_shares(sale.remaining)} "
             f"{_plural(sale.remaining)}."
    )
    message += f" Cash available: ${sale.cash:,.2f}."
    return schemas.SaleOut(**sale._asdict(), message=message)

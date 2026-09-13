"""Portfolio operations shared by the terminal and web front ends.

This module holds the logic that sits above the models: validating a
quantity, pricing a buy, checking a sale against what is actually owned,
moving cash, and running the bulk refresh jobs. It returns plain results
and never prints or renders, so the CLI can format them for a terminal
and Flask can format the same results for a page.

Every operation takes an explicit `user_id`. Resolving *which* account
that is belongs to the front end: the web app reads it from the session,
the CLI from PORTFOLIO_USER in .env.

No SQL and no yfinance calls live here — those stay in models/ and
services/ respectively.
"""
from collections import namedtuple
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from portfolio_tracker import charts
from portfolio_tracker.errors import (
    InsufficientFunds, UnknownUser, ValidationError,
)
from portfolio_tracker.models import history, portfolio, stocks, users
from portfolio_tracker.models.portfolio import to_money
from portfolio_tracker.services import market_data

# Shares are stored as NUMERIC, so a position can be fractional. Four
# decimal places is the granularity we quote and accept.
SHARE_PRECISION = Decimal("0.0001")

__all__ = [
    "ValidationError", "InsufficientFunds", "UnknownUser",
    "format_shares", "sellable_maximum", "affordable_maximum", "parse_quantity",
    "look_up", "buy", "sell", "refresh_prices", "load_all_history",
    "account_summary",
]

SymbolOutcome = namedtuple("SymbolOutcome", "symbol ok message")
RefreshReport = namedtuple("RefreshReport", "outcomes updated failed total")
HistoryReport = namedtuple("HistoryReport", "outcomes new_rows total")
Purchase = namedtuple(
    "Purchase",
    "symbol company_name price shares cost total_shares average_cost cash",
)
Sale = namedtuple("Sale", "symbol shares price proceeds gain remaining closed cash")
Quote = namedtuple(
    "Quote", "symbol company_name price owned average_cost cash affordable"
)
Summary = namedtuple(
    "Summary",
    "rows unpriced cash holdings_value total_cost total_gain total_percent net_worth",
)


def format_shares(value):
    """Trim trailing zeros: 10.000 -> 10, 2.50 -> 2.5"""
    value = Decimal(value).normalize()
    if value == value.to_integral_value():
        # normalize() renders 100 as 1E+2; quantize expands it back out.
        value = value.quantize(Decimal(1))
    return f"{value:f}"


def sellable_maximum(shares):
    """The owned quantity as a figure that is always safe to submit back.

    Truncated, never rounded: rounding 1.99999 up to 2.0 would show a
    maximum that the sale validation then rejects as an oversell.
    """
    return Decimal(shares).quantize(SHARE_PRECISION, rounding=ROUND_DOWN)


def affordable_maximum(cash, price):
    """How many shares the balance covers — truncated for the same reason.

    Rounding up here would display a quantity that the affordability check
    then refuses as too expensive.
    """
    if cash is None or price is None or Decimal(price) <= 0:
        return Decimal(0)
    return (Decimal(cash) / Decimal(price)).quantize(
        SHARE_PRECISION, rounding=ROUND_DOWN
    )


def parse_quantity(raw, maximum=None, max_label=None):
    """Turn user input into a usable share quantity.

    Raises ValidationError with a readable message rather than returning
    a sentinel, so every caller is forced to handle bad input.

    NaN and Infinity parse as perfectly valid Decimals and slip past a
    plain "<= 0" test, so is_finite() is what actually rejects them.
    """
    if raw is None:
        raise ValidationError("Enter the number of shares.")

    text = str(raw).strip()
    if not text:
        raise ValidationError("Enter the number of shares.")

    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValidationError(f"'{text}' is not a number.")

    if not value.is_finite():
        raise ValidationError("That is not a real quantity.")
    if value <= 0:
        raise ValidationError("Enter a number greater than zero.")
    if maximum is not None and value > maximum:
        label = max_label or format_shares(sellable_maximum(maximum))
        raise ValidationError(f"You only own {label} shares.")

    return value


def look_up(user_id, symbol):
    """Price a ticker, and report the position and buying power behind it.

    Raises ValidationError for an empty or unrecognised ticker so the
    caller can show the message as-is.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValidationError("Enter a ticker symbol.")

    try:
        price, company_name = market_data.get_quote(symbol)
    except Exception as exc:
        raise ValidationError(f"Could not look up {symbol}: {exc}")

    if price is None:
        raise ValidationError(
            f"No market data found for '{symbol}'. Check the ticker and try again."
        )

    held = portfolio.get_holding(user_id, symbol)
    owned, average_cost = held if held else (None, None)
    cash = users.get_cash(user_id)
    return Quote(
        symbol, company_name, price, owned, average_cost,
        cash, affordable_maximum(cash, price),
    )


def buy(user_id, symbol, shares):
    """Buy at the live market price, paying from the account's cash.

    The price is re-fetched here rather than trusted from the caller, so a
    quote shown on a confirmation page cannot be replayed or edited into a
    different cost basis. Affordability is re-checked inside the database
    transaction, not here, so it cannot be raced.
    """
    quote = look_up(user_id, symbol)
    shares = parse_quantity(shares)

    total_shares, average_cost, cash = portfolio.record_purchase(
        user_id, quote.symbol, quote.company_name, quote.price, shares
    )
    return Purchase(
        symbol=quote.symbol,
        company_name=quote.company_name,
        price=quote.price,
        shares=shares,
        cost=to_money(shares * quote.price),
        total_shares=total_shares,
        average_cost=average_cost,
        cash=cash,
    )


def sell(user_id, symbol, shares):
    """Sell part or all of a position, crediting the proceeds to cash."""
    symbol = (symbol or "").strip().upper()
    held = portfolio.get_holding(user_id, symbol)
    if held is None:
        raise ValidationError(f"You do not own any {symbol}.")

    owned, cost_basis = held
    shares = parse_quantity(shares, maximum=owned)

    try:
        price = market_data.get_live_price(symbol)
    except Exception as exc:
        raise ValidationError(f"Could not look up {symbol}: {exc}")
    if price is None:
        raise ValidationError(f"No current price for {symbol}. Nothing was sold.")

    sold, remaining, cash = portfolio.record_sale(user_id, symbol, price, shares)
    if not sold:
        raise ValidationError(
            "That sale is no longer valid — your position may have changed."
        )

    return Sale(
        symbol=symbol,
        shares=sold,
        price=price,
        proceeds=to_money(sold * price),
        gain=to_money((price - cost_basis) * sold),
        remaining=remaining,
        closed=remaining == 0,
        cash=cash,
    )


def account_summary(user_id):
    """Everything the balance view needs, in one pass.

    Cash plus the market value of the holdings is the account's net worth —
    the figure that actually answers "how am I doing overall". Holdings with
    no quote yet are listed but kept out of the totals rather than counted
    as zero.
    """
    rows = []
    holdings_value = Decimal(0)
    priced_cost = Decimal(0)
    unpriced = []

    holdings = portfolio.get_holdings_with_details(user_id)
    for symbol, name, held, paid, current, gain in holdings:
        cost = held * paid
        row = {
            "symbol": symbol,
            "company_name": name or symbol,
            "shares": held,
            "purchase_price": paid,
            "current_price": current,
            "cost": cost,
            "value": None,
            "gain_loss": None,
            "percent": None,
        }
        if current is None:
            unpriced.append(symbol)
        else:
            row["value"] = held * current
            row["gain_loss"] = gain
            row["percent"] = (gain / cost * 100) if cost else Decimal(0)
            holdings_value += row["value"]
            priced_cost += cost
        rows.append(row)

    cash = users.get_cash(user_id) or Decimal(0)
    total_gain = holdings_value - priced_cost
    return Summary(
        rows=rows,
        unpriced=unpriced,
        cash=cash,
        holdings_value=holdings_value,
        total_cost=priced_cost,
        total_gain=total_gain,
        total_percent=(total_gain / priced_cost * 100) if priced_cost else Decimal(0),
        net_worth=cash + holdings_value,
    )


def refresh_prices(user_id, on_start=None):
    """Re-quote every held symbol. One bad ticker must not stop the rest.

    on_start(symbol) fires before each lookup so a caller that wants
    progress output can render it; this module stays silent either way.
    """
    symbols = portfolio.get_portfolio_symbols(user_id)
    outcomes = []
    updated = failed = 0

    for symbol in symbols:
        if on_start:
            on_start(symbol)
        try:
            price = market_data.get_live_price(symbol)
            if price is None:
                outcomes.append(SymbolOutcome(symbol, False, "no price data available"))
                failed += 1
                continue
            stocks.update_stock_price(symbol, price)
            outcomes.append(SymbolOutcome(symbol, True, f"${price:,.2f}"))
            updated += 1
        except Exception as exc:
            outcomes.append(SymbolOutcome(symbol, False, f"error: {exc}"))
            failed += 1

    return RefreshReport(outcomes, updated, failed, len(symbols))


def load_all_history(user_id, period="6mo", on_start=None):
    """Pull daily history for every held symbol.

    on_start(symbol) fires before each download, which the terminal uses
    to show progress during what can be a slow run.
    """
    symbols = portfolio.get_portfolio_symbols(user_id)
    outcomes = []
    new_rows = 0

    for symbol in symbols:
        if on_start:
            on_start(symbol)
        try:
            count = history.load_history_for_symbol(symbol, period=period)
            new_rows += count
            message = f"{count} new rows" if count else "already up to date"
            outcomes.append(SymbolOutcome(symbol, True, message))
        except Exception as exc:
            outcomes.append(SymbolOutcome(symbol, False, f"error: {exc}"))

    return HistoryReport(outcomes, new_rows, len(symbols))


# ------------------------------------------------------------- assistant

_CONTEXT_RANGES = ("1m", "6m", "1y", "all")


def _money(value):
    return "unknown" if value is None else f"${Decimal(value):,.2f}"


def _range_lines(symbol):
    """One line per stored price range, from the same series the charts use."""
    from datetime import date, timedelta

    lines = []
    for key in _CONTEXT_RANGES:
        spec = charts.RANGES[key]
        since = None if spec["days"] is None else date.today() - timedelta(days=spec["days"])
        chart = charts.build(history.get_series(symbol, since))
        if chart is None or chart.count < 2:
            continue
        lines.append(
            f"- {spec['label']}: from {_money(chart.first)} to {_money(chart.last)} "
            f"({chart.change:+,.2f}, {chart.change_pct:+.1f}%), "
            f"low {_money(chart.low)}, high {_money(chart.high)}, "
            f"average {_money(chart.average)}, {chart.count} trading days stored"
        )

    session = charts.build(history.get_intraday_sessions(symbol, 1))
    if session is not None and session.count >= 2:
        lines.append(
            f"- latest trading session: from {_money(session.first)} to "
            f"{_money(session.last)} ({session.change:+,.2f}), "
            f"low {_money(session.low)}, high {_money(session.high)}"
        )
    return lines


def assistant_context(user_id, symbol=None):
    """The facts the assistant is allowed to cite, as plain text.

    Built fresh for every question from this app's own records and never
    from anything the browser sends, so a figure in an answer can be traced
    back to something the app actually shows. Only this user's holdings are
    included.
    """
    from datetime import date

    summary = account_summary(user_id)
    lines = [
        f"Date: {date.today().isoformat()}",
        "Money in this app is pretend; prices are real.",
        f"Cash available: {_money(summary.cash)}",
        f"Value of investments: {_money(summary.holdings_value)}",
        f"Total account value: {_money(summary.net_worth)}",
    ]

    symbol = (symbol or "").strip().upper()
    if symbol:
        lines.append("")
        lines.append(f"The person is looking at {symbol}.")
        try:
            quote = look_up(user_id, symbol)
        except ValidationError as exc:
            lines.append(f"No market data could be found for {symbol}: {exc}")
        else:
            lines.append(f"Company: {quote.company_name}")
            lines.append(f"Current price: {_money(quote.price)} per share")
            if quote.owned is not None:
                value = quote.owned * quote.price
                cost = quote.owned * quote.average_cost
                change = value - cost
                share = (value / summary.net_worth * 100) if summary.net_worth else Decimal(0)
                lines.append(
                    f"Their position: {format_shares(quote.owned)} shares at an "
                    f"average cost of {_money(quote.average_cost)}; worth "
                    f"{_money(value)} now against {_money(cost)} paid "
                    f"({change:+,.2f}); {share:.1f}% of their total account"
                )
            else:
                lines.append(
                    f"They do not own any. Their cash would buy up to "
                    f"{format_shares(quote.affordable)} shares."
                )
            history_lines = _range_lines(symbol)
            if history_lines:
                lines.append("Stored price history:")
                lines.extend(history_lines)
            else:
                lines.append("No price history is stored for this company yet.")

    lines.append("")
    if summary.rows:
        lines.append("Their holdings:")
        for row in summary.rows:
            if row["current_price"] is None:
                lines.append(
                    f"- {row['symbol']} ({row['company_name']}): "
                    f"{format_shares(row['shares'])} shares, no current price stored"
                )
            else:
                lines.append(
                    f"- {row['symbol']} ({row['company_name']}): "
                    f"{format_shares(row['shares'])} shares, paid "
                    f"{_money(row['purchase_price'])} on average, now "
                    f"{_money(row['current_price'])}, worth {_money(row['value'])} "
                    f"({row['gain_loss']:+,.2f})"
                )
    else:
        lines.append("They do not own any stocks yet.")

    return "\n".join(lines)


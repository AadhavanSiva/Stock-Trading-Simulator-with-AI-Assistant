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
import logging
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from portfolio_tracker import charts
from portfolio_tracker.errors import (
    InsufficientFunds, MarketDataUnavailable, UnknownUser, ValidationError,
)
from portfolio_tracker.models import (
    assistant_usage, history, portfolio, stocks, trades, users, value_history,
)
from portfolio_tracker.models.portfolio import to_money
from portfolio_tracker.services import market_data

log = logging.getLogger(__name__)

# Shares are stored as NUMERIC, so a position can be fractional. Four
# decimal places is the granularity we quote and accept.
SHARE_PRECISION = Decimal("0.0001")

# What someone has to type out before their account is erased. A button
# alone is one mis-click from irreversible; typing the word takes a
# deliberate act that a stray click cannot produce.
DELETE_CONFIRMATION = "DELETE"

__all__ = [
    "ValidationError", "InsufficientFunds", "UnknownUser",
    "format_shares", "sellable_maximum", "affordable_maximum", "parse_quantity",
    "look_up", "buy", "sell", "refresh_prices", "load_all_history",
    "account_summary", "assistant_wait_seconds",
    "recent_activity", "trade_history", "export_account", "delete_account",
    "record_value_sample", "performance", "percent_return",
]


def _market_failure(symbol, exc):
    """What a person is told when a market-data call fails.

    The underlying error (a network or library message) is logged, never
    shown: it means nothing to a reader and can expose internals.
    MarketDataUnavailable was already logged where it was raised.
    """
    if not isinstance(exc, MarketDataUnavailable):
        log.error("Market data call for %s failed unexpectedly", symbol, exc_info=exc)
    return str(MarketDataUnavailable(symbol))


def _job_failure(symbol, exc):
    """A short per-symbol note for the refresh and history reports."""
    if isinstance(exc, MarketDataUnavailable):
        return "Yahoo Finance didn't respond; try again in a minute"
    log.error("Bulk job failed for %s", symbol, exc_info=exc)
    return "something went wrong; the error has been logged"


SymbolOutcome = namedtuple("SymbolOutcome", "symbol ok message")
RefreshReport = namedtuple("RefreshReport", "outcomes updated failed total")
HistoryReport = namedtuple("HistoryReport", "outcomes new_rows total")
Purchase = namedtuple(
    "Purchase",
    "symbol company_name price shares cost total_shares average_cost cash",
)
Sale = namedtuple("Sale", "symbol shares price proceeds gain remaining closed cash")
Quote = namedtuple(
    "Quote",
    "symbol company_name price owned average_cost cash affordable previous_close"
)
Summary = namedtuple(
    "Summary",
    "rows unpriced cash holdings_value total_cost total_gain total_percent net_worth "
    "realized activity todays_change todays_percent",
)
TradeRow = namedtuple(
    "TradeRow",
    "id symbol company_name side shares price total_value cost_basis gain "
    "opening traded_at",
)
TradePage = namedtuple("TradePage", "rows page pages total page_size")
# Gains that have actually been banked, as opposed to the paper gain on a
# position still open. `closed` covers tickers the account has sold out of
# entirely — they have a realized result but no holding left to show it on.
Realized = namedtuple("Realized", "total by_symbol closed")


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


def look_up(user_id, symbol, fresh=False):
    """Price a ticker, and report the position and buying power behind it.

    Raises ValidationError for an empty or unrecognised ticker, or when the
    price cannot be fetched, so the caller can show the message as-is.
    Pages may use a quote cached for a few seconds; pass fresh=True for
    anything that trades on the price.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValidationError("Enter a ticker symbol.")

    try:
        price, company_name, previous_close = market_data.get_quote(symbol, fresh=fresh)
    except Exception as exc:
        raise ValidationError(_market_failure(symbol, exc)) from exc

    if price is None:
        raise ValidationError(
            f"No market data found for '{symbol}'. Check the ticker and try again."
        )

    held = portfolio.get_holding(user_id, symbol)
    owned, average_cost = held if held else (None, None)
    cash = users.get_cash(user_id)
    return Quote(
        symbol, company_name, price, owned, average_cost,
        cash, affordable_maximum(cash, price), previous_close,
    )


def buy(user_id, symbol, shares):
    """Buy at the live market price, paying from the account's cash.

    The price is re-fetched here rather than trusted from the caller, so a
    quote shown on a confirmation page cannot be replayed or edited into a
    different cost basis. Affordability is re-checked inside the database
    transaction, not here, so it cannot be raced.
    """
    quote = look_up(user_id, symbol, fresh=True)
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

    owned = held[0]
    shares = parse_quantity(shares, maximum=owned)

    try:
        price = market_data.get_live_price(symbol, fresh=True)
    except Exception as exc:
        raise ValidationError(_market_failure(symbol, exc)) from exc
    if price is None:
        raise ValidationError(f"No current price for {symbol}. Nothing was sold.")

    result = portfolio.record_sale(user_id, symbol, price, shares)
    if not result.sold:
        raise ValidationError(
            "That sale is no longer valid — your position may have changed."
        )

    return Sale(
        symbol=symbol,
        shares=result.sold,
        price=price,
        proceeds=to_money(result.sold * price),
        # From the basis the sale itself locked, not the one read before the
        # transaction started: a buy landing in between would change the
        # average, and the figure reported here has to be the one recorded.
        gain=to_money((price - result.cost_basis) * result.sold),
        remaining=result.remaining,
        closed=result.remaining == 0,
        cash=result.cash,
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

    todays_change = Decimal(0)
    todays_basis = Decimal(0)

    holdings = portfolio.get_holdings_with_details(user_id)
    for symbol, name, held, paid, current, gain, previous_close in holdings:
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
            "previous_close": previous_close,
            "todays_change": None,
            "todays_percent": None,
        }
        if current is None:
            unpriced.append(symbol)
        else:
            row["value"] = held * current
            row["gain_loss"] = gain
            row["percent"] = (gain / cost * 100) if cost else Decimal(0)
            holdings_value += row["value"]
            priced_cost += cost

            # Today's move, where a previous close is known. A position
            # bought this morning has no close to compare against, and its
            # change stays None rather than becoming a zero that reads as
            # "unchanged today".
            if previous_close is not None:
                row["todays_change"] = (current - previous_close) * held
                row["todays_percent"] = (
                    (current - previous_close) / previous_close * 100
                    if previous_close else None
                )
                todays_change += row["todays_change"]
                todays_basis += previous_close * held
        rows.append(row)

    # Realized gain is read from the trade log, which is the only place it
    # exists: a position's own row remembers what it cost, not what earlier
    # sales of it returned. Attaching it per row lets a holding show both
    # halves of its result — what it has banked and what it is still
    # carrying — which a single "gain" figure cannot.
    realized = realized_summary(user_id, held_symbols=[row["symbol"] for row in rows])
    for row in rows:
        row["realized"] = realized.by_symbol.get(row["symbol"])

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
        realized=realized,
        activity=recent_activity(user_id),
        # None, not zero, when nothing held has a previous close yet: the
        # honest answer is "not known", and a zero would claim a flat day.
        todays_change=todays_change if todays_basis else None,
        todays_percent=(todays_change / todays_basis * 100) if todays_basis else None,
    )


def realized_summary(user_id, held_symbols=None):
    """What the account has actually banked, per ticker and in total.

    A ticker sold out of entirely still has a realized result but no
    holding left to hang it on, so those are separated into `closed` for
    the views to list on their own.
    """
    rows = trades.realized_by_symbol(user_id)
    if held_symbols is None:
        held_symbols = portfolio.get_portfolio_symbols(user_id)
    held = set(held_symbols)

    return Realized(
        total=sum((gain for gain, _, _ in rows.values()), Decimal(0)),
        by_symbol={symbol: gain for symbol, (gain, _, _) in rows.items()},
        closed=[
            {"symbol": symbol, "realized": gain,
             "proceeds": proceeds, "shares_sold": sold}
            for symbol, (gain, proceeds, sold) in rows.items()
            if symbol not in held
        ],
    )


# ------------------------------------------------------------ the ledger

def _trade_row(row):
    """One database row as the namedtuple the views render."""
    (trade_id, symbol, company_name, side, shares, price, total_value,
     cost_basis, backfilled, traded_at) = row
    return TradeRow(
        id=trade_id,
        symbol=symbol,
        company_name=company_name or symbol,
        side=side,
        shares=shares,
        price=price,
        total_value=total_value,
        cost_basis=cost_basis,
        # Only a sale settles a gain, so a buy's is None rather than zero —
        # zero would read as "broke even", which is a different claim.
        gain=to_money((price - cost_basis) * shares) if cost_basis is not None else None,
        opening=backfilled,
        traded_at=traded_at,
    )


def recent_activity(user_id, limit=5):
    """The last few trades, for the portfolio page."""
    return [_trade_row(row) for row in trades.recent(user_id, limit=limit)]


def trade_history(user_id, page=1, page_size=trades.PAGE_SIZE):
    """One page of the full trade history, newest first.

    Out-of-range pages clamp to the last real page rather than returning
    nothing, so a stale link or a hand-edited ?page= shows the end of the
    history instead of an empty table.
    """
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1

    total = trades.count(user_id)
    pages = max(1, -(-total // page_size))   # ceiling division
    page = min(max(1, page), pages)

    rows = trades.page(user_id, page_number=page, page_size=page_size)
    return TradePage(
        rows=[_trade_row(row) for row in rows],
        page=page,
        pages=pages,
        total=total,
        page_size=page_size,
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
            price, _, previous_close = market_data.get_quote(symbol, fresh=True)
            if price is None:
                outcomes.append(SymbolOutcome(symbol, False, "no price data available"))
                failed += 1
                continue
            # Both from the one quote, so the close and the price they are
            # compared against describe the same moment.
            stocks.update_stock_price(symbol, price, previous_close=previous_close)
            outcomes.append(SymbolOutcome(symbol, True, f"${price:,.2f}"))
            updated += 1
        except Exception as exc:
            outcomes.append(SymbolOutcome(symbol, False, _job_failure(symbol, exc)))
            failed += 1

    # Sampled after the prices move, not before, so the figure recorded is
    # the one the refresh just produced. Taken even when every symbol
    # failed: a flat stretch in the chart is the honest record of a day the
    # market data could not be reached, and a gap would be read as no
    # change rather than no reading.
    record_value_sample(user_id)

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
            outcomes.append(SymbolOutcome(symbol, False, _job_failure(symbol, exc)))

    return HistoryReport(outcomes, new_rows, len(symbols))


# ------------------------------------------------------------- assistant

def assistant_wait_seconds(user_id, limit, window_seconds):
    """Reserve one assistant question for this account, if it is allowed.

    Returns 0 when the question may go ahead (and counts it), or the
    number of seconds until the account may ask again. Every question
    costs an API call, so the count is kept in the database, where it
    survives restarts and is shared by every server process.
    """
    return assistant_usage.reserve_question(user_id, limit, window_seconds)


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



# --------------------------------------------------- export and erasure

def _plain(value):
    """A value json.dumps can serialize, without losing exactness.

    Decimals become strings rather than floats: a cost basis of 164.20 must
    survive the round trip as 164.20, and float() would hand back
    164.19999999999999. Timestamps become ISO-8601, which is unambiguous
    about the offset in a way a local-time string is not.
    """
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def export_account(user_id):
    """Everything the account holds, as a JSON-ready dict.

    This is the person's own data and nothing else: their profile, their
    cash, their positions and their full trade history. Shared market data
    is deliberately left out — price history is not theirs to take, and
    including it would bury the part that is.
    """
    account = users.get_by_id(user_id)
    if account is None:
        raise UnknownUser(f"No account with id {user_id}.")

    _, google_sub, email, display_name, cash = account
    summary = account_summary(user_id)
    log = trade_history(user_id, page=1, page_size=trades.count(user_id) or 1)

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "format_version": 1,
        "account": {
            "email": email,
            "display_name": display_name,
            "google_sub": google_sub,
            "cash": _plain(cash),
        },
        "holdings": [
            {
                "symbol": row["symbol"],
                "company_name": row["company_name"],
                "shares": _plain(row["shares"]),
                "average_cost": _plain(row["purchase_price"]),
                "current_price": _plain(row["current_price"]),
                "market_value": _plain(row["value"]),
                "unrealized_gain": _plain(row["gain_loss"]),
                "realized_gain": _plain(row["realized"]),
            }
            for row in summary.rows
        ],
        "trades": [
            {
                "symbol": row.symbol,
                "company_name": row.company_name,
                "side": row.side,
                "shares": _plain(row.shares),
                "price": _plain(row.price),
                "total_value": _plain(row.total_value),
                "cost_basis": _plain(row.cost_basis),
                "realized_gain": _plain(row.gain),
                # Flagged so a reader is not misled into treating a
                # reconstructed opening position as a real recorded trade.
                "opening_position": row.opening,
                "traded_at": _plain(row.traded_at),
            }
            for row in log.rows
        ],
        "totals": {
            "cash": _plain(summary.cash),
            "holdings_value": _plain(summary.holdings_value),
            "net_worth": _plain(summary.net_worth),
            "unrealized_gain": _plain(summary.total_gain),
            "realized_gain": _plain(summary.realized.total),
            "percent_return": _plain(percent_return(user_id, summary=summary)),
        },
    }


def delete_account(user_id, confirmation):
    """Permanently erase an account, once the person has typed the words.

    The typed confirmation is required by the caller's own UI, but it is
    re-checked here so the rule does not live only in a template: any front
    end that grows a delete button gets the same guard. Comparison ignores
    surrounding space and case, which are not what we are testing for.

    Returns the deleted account's email.
    """
    if (confirmation or "").strip().lower() != DELETE_CONFIRMATION.lower():
        raise ValidationError(
            f'Type "{DELETE_CONFIRMATION}" exactly to confirm that you want '
            f"to delete your account. Nothing has been deleted."
        )

    email = users.delete_account(user_id)
    if email is None:
        raise UnknownUser("That account no longer exists.")
    log.info("Account %s deleted at the owner's request", user_id)
    return email


# --------------------------------------------------- value over time

Performance = namedtuple(
    "Performance",
    "chart start_value end_value change percent samples first_at last_at",
)


def record_value_sample(user_id):
    """Store what the account is worth right now.

    Valued from stored prices, not live ones: this runs straight after a
    refresh has written them, and re-quoting here would both double the
    market-data calls and risk recording a figure the page never showed.
    """
    summary = account_summary(user_id)
    return value_history.record(user_id, summary.cash, summary.holdings_value)


def percent_return(user_id, summary=None):
    """Growth against the cash the account opened with.

    Measured from the starting balance rather than the first sample,
    because that is the question a paper-trading account actually poses:
    you were handed a fixed sum and nothing is ever paid in or out, so
    "what did you turn it into" needs no time-weighting to be fair. It also
    makes two accounts comparable however long each has been open, which is
    what the leaderboard needs.

    Returns None when the starting balance is zero, rather than dividing.
    """
    if summary is None:
        summary = account_summary(user_id)
    opening = users.starting_cash()
    if not opening:
        return None
    return (summary.net_worth - opening) / opening * 100


def performance(user_id, days=None, width=720, height=220):
    """The account's value over time, ready to plot.

    Returns None when there is nothing to draw yet — one sample is a dot,
    not a line, and the page says so instead of rendering an empty frame.
    """
    since = None
    if days:
        since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = value_history.series(user_id, since=since)
    if len(rows) < 2:
        return None

    chart = charts.build(
        [(charts.point_label(recorded_at), total) for recorded_at, _, _, total in rows],
        width=width, height=height,
    )
    if chart is None:
        return None

    start_value, end_value = rows[0][3], rows[-1][3]
    change = end_value - start_value
    return Performance(
        chart=chart,
        start_value=start_value,
        end_value=end_value,
        change=change,
        percent=(change / start_value * 100) if start_value else None,
        samples=len(rows),
        first_at=rows[0][0],
        last_at=rows[-1][0],
    )

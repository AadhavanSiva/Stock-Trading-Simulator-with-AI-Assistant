"""Request and response shapes.

Money, prices and share counts are Decimal and travel as JSON strings
("1234.50"), never numbers. JSON has no decimal type, and routing money
through a float to serialise it is exactly the bug the rest of this
codebase avoids. Pydantic serialises Decimal to a string on its own; the
web UI formats those strings for display without doing arithmetic on them.
"""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from portfolio_tracker import charts
from portfolio_tracker.models import users


# ------------------------------------------------------------------ auth

class AuthConfig(BaseModel):
    """What the sign-in page needs to know before anyone is signed in."""
    google_client_id: str | None
    dev_login: bool
    starting_cash: Decimal


class GoogleSignIn(BaseModel):
    credential: str = Field(min_length=1, description="The ID token from Google Identity Services.")


class DevSignIn(BaseModel):
    email: str = Field(min_length=1, max_length=254)


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    cash: Decimal
    starting_cash: Decimal


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    new_account: bool
    user: UserOut


def user_out(user):
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name or user.email,
        cash=user.cash,
        starting_cash=users.starting_cash(),
    )


# ---------------------------------------------------------------- charts

class YTick(BaseModel):
    value: Decimal
    label: str
    y: float
    pct: float


class XTick(BaseModel):
    label: str
    pct: float
    edge: str


class Readout(BaseModel):
    """One hoverable point: position as a percentage of the plot."""
    x: float
    y: float
    when: str
    price: str


class ChartOut(BaseModel):
    """A series already projected onto a width x height viewBox.

    The projection, axis ticks and thinning are done server-side by
    portfolio_tracker.charts, so the UI only draws paths and labels.
    """
    width: int
    height: int
    path: str
    area_path: str
    baseline_y: float
    low: Decimal
    high: Decimal
    average: Decimal
    first: Decimal
    last: Decimal
    change: Decimal
    change_pct: Decimal
    count: int
    plotted: int
    y_ticks: list[YTick]
    x_ticks: list[XTick]
    readouts: list[Readout]


def chart_out(chart):
    if chart is None:
        return None
    return ChartOut(
        width=chart.width, height=chart.height,
        path=chart.path, area_path=chart.area_path, baseline_y=chart.baseline_y,
        low=chart.low, high=chart.high, average=chart.average,
        first=chart.first, last=chart.last,
        change=chart.change, change_pct=chart.change_pct,
        count=chart.count, plotted=chart.plotted,
        y_ticks=[YTick(**tick) for tick in chart.y_ticks],
        x_ticks=[XTick(**tick) for tick in chart.x_ticks],
        readouts=[Readout(x=x, y=y, when=when, price=price)
                  for x, y, when, price in charts.readouts(chart)],
    )


# ------------------------------------------------------------- portfolio

class Holding(BaseModel):
    symbol: str
    company_name: str
    shares: Decimal
    purchase_price: Decimal
    current_price: Decimal | None
    cost: Decimal
    value: Decimal | None
    gain_loss: Decimal | None
    percent: Decimal | None
    previous_close: Decimal | None
    todays_change: Decimal | None
    todays_percent: Decimal | None
    realized: Decimal | None


class Trade(BaseModel):
    id: int
    symbol: str
    company_name: str
    side: str
    shares: Decimal
    price: Decimal
    total_value: Decimal
    cost_basis: Decimal | None
    gain: Decimal | None
    opening: bool
    traded_at: datetime


def trade_out(row):
    return Trade(**row._asdict())


class ClosedPosition(BaseModel):
    symbol: str
    realized: Decimal
    proceeds: Decimal
    shares_sold: Decimal


class PerformanceOut(BaseModel):
    chart: ChartOut
    start_value: Decimal
    end_value: Decimal
    change: Decimal
    percent: Decimal | None
    samples: int
    first_at: datetime
    last_at: datetime


class PortfolioOut(BaseModel):
    holdings: list[Holding]
    unpriced: list[str]
    cash: Decimal
    holdings_value: Decimal
    total_cost: Decimal
    total_gain: Decimal
    total_percent: Decimal
    net_worth: Decimal
    starting_cash: Decimal
    total_return: Decimal | None
    todays_change: Decimal | None
    todays_percent: Decimal | None
    realized_total: Decimal
    closed_positions: list[ClosedPosition]
    activity: list[Trade]
    performance: PerformanceOut | None
    prices_as_of: datetime | None


def portfolio_out(summary, performance, total_return, prices_as_of):
    return PortfolioOut(
        holdings=[Holding(**row) for row in summary.rows],
        unpriced=summary.unpriced,
        cash=summary.cash,
        holdings_value=summary.holdings_value,
        total_cost=summary.total_cost,
        total_gain=summary.total_gain,
        total_percent=summary.total_percent,
        net_worth=summary.net_worth,
        starting_cash=users.starting_cash(),
        total_return=total_return,
        todays_change=summary.todays_change,
        todays_percent=summary.todays_percent,
        realized_total=summary.realized.total,
        closed_positions=[ClosedPosition(**row) for row in summary.realized.closed],
        activity=[trade_out(row) for row in summary.activity],
        performance=(
            PerformanceOut(
                chart=chart_out(performance.chart),
                **{k: v for k, v in performance._asdict().items() if k != "chart"},
            )
            if performance else None
        ),
        prices_as_of=prices_as_of,
    )


# ---------------------------------------------------------------- trades

class TradePageOut(BaseModel):
    """One page of the trade log, newest first, with what selling has banked."""
    trades: list[Trade]
    page: int
    pages: int
    total: int
    page_size: int
    realized_total: Decimal
    closed_positions: list[ClosedPosition]


def trade_page_out(log, realized):
    return TradePageOut(
        trades=[trade_out(row) for row in log.rows],
        page=log.page, pages=log.pages, total=log.total, page_size=log.page_size,
        realized_total=realized.total,
        closed_positions=[ClosedPosition(**row) for row in realized.closed],
    )


# --------------------------------------------------------------- history

class PriceRange(BaseModel):
    """Today's price beside the recent closes it can be judged against."""
    symbol: str
    company_name: str
    current_price: Decimal | None
    # None until that company's past prices have been downloaded.
    average: Decimal | None
    high: Decimal | None
    low: Decimal | None


class HistoryOut(BaseModel):
    days: int
    rows: list[PriceRange]


# --------------------------------------------------------------- trading

class SearchResult(BaseModel):
    symbol: str
    name: str


class QuoteOut(BaseModel):
    symbol: str
    company_name: str
    price: Decimal
    previous_close: Decimal | None
    owned: Decimal | None
    average_cost: Decimal | None
    cash: Decimal
    affordable: Decimal


def quote_out(quote):
    return QuoteOut(
        symbol=quote.symbol,
        company_name=quote.company_name or quote.symbol,
        price=quote.price,
        previous_close=quote.previous_close,
        owned=quote.owned,
        average_cost=quote.average_cost,
        cash=quote.cash,
        affordable=quote.affordable,
    )


class TradeRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    # A string, not a number: the quantity is validated and parsed as a
    # Decimal by operations.parse_quantity, whose messages are written for
    # a person. An int is accepted for convenience; a float is not.
    shares: str | int

    def raw_shares(self):
        return str(self.shares)


class BuyPreview(BaseModel):
    """What a purchase would cost at the current price. Nothing is written."""
    quote: QuoteOut
    shares: Decimal
    cost: Decimal
    cash_after: Decimal


class PurchaseOut(BaseModel):
    symbol: str
    company_name: str
    price: Decimal
    shares: Decimal
    cost: Decimal
    total_shares: Decimal
    average_cost: Decimal
    cash: Decimal
    message: str


class SellQuote(BaseModel):
    """A position and today's price, for choosing how much to sell."""
    symbol: str
    company_name: str
    owned: Decimal
    # Truncated, never rounded: a rounded-up maximum would be rejected as
    # an oversell the moment it was submitted back.
    maximum: Decimal
    average_cost: Decimal
    price: Decimal
    cash: Decimal


class SellPreview(BaseModel):
    symbol: str
    company_name: str
    shares: Decimal
    price: Decimal
    proceeds: Decimal
    gain: Decimal
    closes: bool
    cash_after: Decimal


class SaleOut(BaseModel):
    symbol: str
    shares: Decimal
    price: Decimal
    proceeds: Decimal
    gain: Decimal
    remaining: Decimal
    closed: bool
    cash: Decimal
    message: str


# ---------------------------------------------------------------- stocks

class RangeOption(BaseModel):
    key: str
    label: str


class PriceStats(BaseModel):
    low: Decimal | None
    high: Decimal | None
    average: Decimal | None


class StockDetail(BaseModel):
    symbol: str
    quote: QuoteOut
    watching: bool
    prices_as_of: datetime | None
    earliest_available: date
    range: str
    range_label: str
    ranges: list[RangeOption]
    intraday: bool
    chart: ChartOut | None
    needs_fetch: bool
    partial: bool
    stored_since: date | None
    stats: PriceStats


class ChartFetchRequest(BaseModel):
    range: str | None = None


class ChartFetchOut(BaseModel):
    symbol: str
    range: str
    added: int
    message: str



# ------------------------------------------------------------- watchlist

class WatchRow(BaseModel):
    symbol: str
    company_name: str
    price: Decimal | None
    previous_close: Decimal | None
    # None until a previous close has been recorded for the ticker.
    todays_change: Decimal | None
    todays_percent: Decimal | None
    owned: bool
    added_at: datetime


class WatchlistOut(BaseModel):
    rows: list[WatchRow]
    prices_as_of: datetime | None


class WatchRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=64)


class WatchChange(BaseModel):
    symbol: str
    # False when the request changed nothing: already followed, or not on
    # the list to begin with.
    changed: bool
    message: str


class WatchlistRefresh(BaseModel):
    updated: int
    failed: int
    total: int
    message: str


# ----------------------------------------------------------- leaderboard

class LeaderboardRow(BaseModel):
    """All that one player ever shows another: a chosen name and totals."""
    rank: int
    name: str
    total_value: Decimal
    percent_return: Decimal | None
    is_you: bool


class Standing(BaseModel):
    rank: int
    total_value: Decimal
    percent_return: Decimal | None
    opted_in: bool
    participants: int
    as_of: datetime


def standing_out(standing):
    return Standing(**standing._asdict()) if standing else None


class LeaderboardOut(BaseModel):
    rows: list[LeaderboardRow]
    # The reader's own standing, listed or not. None until the account
    # has been valued at least once.
    standing: Standing | None
    starting_cash: Decimal


class LeaderboardSetting(BaseModel):
    opt_in: bool
    name: str | None = Field(None, max_length=200)


class LeaderboardSettingOut(BaseModel):
    opt_in: bool
    name: str | None
    standing: Standing | None
    message: str


# --------------------------------------------------------------- account

class AccountOut(BaseModel):
    email: str
    display_name: str
    cash: Decimal
    starting_cash: Decimal
    trade_count: int
    # The word that has to be typed to delete the account.
    delete_confirmation: str
    leaderboard_opt_in: bool
    leaderboard_name: str | None
    leaderboard_name_max: int
    standing: Standing | None


class DeleteAccount(BaseModel):
    confirmation: str = Field(max_length=64)


class AccountDeleted(BaseModel):
    email: str


# ------------------------------------------------------------- assistant

class AssistantStatus(BaseModel):
    """What the Ask panel needs before the first question."""
    enabled: bool
    # Whether the next question will be offered web search, so the panel
    # only says "searching the web" when it will.
    research: bool
    research_notice: str
    max_question_chars: int


class EarlierTurn(BaseModel):
    question: str = ""
    answer: str = ""


class AssistantQuestion(BaseModel):
    question: str = ""
    # Which stock page the panel is open on. Only the ticker: every figure
    # the model sees is read from this app's records, never from here.
    symbol: str = Field("", max_length=16)
    earlier: list[EarlierTurn] = Field(default_factory=list, max_length=20)


class AssistantSource(BaseModel):
    title: str
    uri: str
    domain: str


class AssistantAnswer(BaseModel):
    ok: bool
    answer: str
    kind: str
    message: str
    # The same text as `message`, so a failure reads like every other
    # error from this API.
    detail: str
    sources: list[AssistantSource]
    # Google's HTML, passed through untouched: its terms require the
    # suggestions to be shown, unmodified, with any searched answer.
    search_suggestions: str
    notice: str
    searches: list[str]
    retry_after: int | None
    research: bool


# --------------------------------------------------------------- actions

class SymbolOutcome(BaseModel):
    symbol: str
    ok: bool
    message: str


class ActionReport(BaseModel):
    title: str
    message: str
    # "success", "notice" or "error" — how the message should be shown.
    kind: str
    total: int
    outcomes: list[SymbolOutcome]

from decimal import Decimal, InvalidOperation

from portfolio_tracker import operations
from portfolio_tracker.errors import InsufficientFunds, UnknownUser, ValidationError
from portfolio_tracker.models import portfolio, users
from portfolio_tracker.operations import format_shares
from portfolio_tracker.reports import print_balance, print_full_report
from portfolio_tracker.services import market_data

CANCEL_WORDS = ("c", "cancel", "q", "quit")
ALL_WORDS = ("a", "all")


class Cancelled(Exception):
    """The user backed out of the current action."""


def prompt(message):
    """input() that treats Ctrl+C / Ctrl+D as a cancel instead of a crash."""
    try:
        return input(message).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise Cancelled()


def ask_decimal(message, maximum=None, max_label=None, allow_all=False):
    """Prompt until the answer is a usable positive Decimal.

    Returns None if the user cancels. NaN and Infinity parse fine as
    Decimals and slip past a plain "<= 0" check, so is_finite() is what
    actually rejects them.
    """
    while True:
        raw = prompt(message)

        if raw.lower() in CANCEL_WORDS:
            return None
        if allow_all and maximum is not None and raw.lower() in ALL_WORDS:
            return maximum
        if not raw:
            print("Enter a number, or 'c' to cancel.")
            continue

        try:
            value = Decimal(raw)
        except InvalidOperation:
            print("That's not a number — try again.")
            continue

        if not value.is_finite():
            print("That's not a real quantity — try again.")
            continue
        if value <= 0:
            print("Must be greater than zero — try again.")
            continue
        if maximum is not None and value > maximum:
            print(f"You only have {max_label or maximum} — try again.")
            continue

        return value


def confirm(message):
    return prompt(f"{message} (y/n): ").lower() in ("y", "yes")


def show_balance(user_id):
    print_balance(user_id)


def update_live_prices(user_id):
    report = operations.refresh_prices(user_id)
    if not report.total:
        print("No holdings yet — nothing to update.")
        return

    for outcome in report.outcomes:
        print(f"  {outcome.symbol:<8} {outcome.message}")

    summary = f"\nUpdated {report.updated} of {report.total}."
    print(summary + (f" {report.failed} failed." if report.failed else ""))


def load_historical_data(user_id):
    if not portfolio.get_portfolio_symbols(user_id):
        print("No holdings yet — nothing to load.")
        return

    report = operations.load_all_history(
        user_id,
        on_start=lambda symbol: print(f"  Pulling history for {symbol}..."),
    )
    for outcome in report.outcomes:
        print(f"  {outcome.symbol:<8} {outcome.message}")

    print(f"\nHistorical data load complete. {report.new_rows} new rows.")


def add_stock(user_id):
    symbol = prompt("Ticker to add (e.g. AAPL): ").upper()
    if not symbol:
        print("No ticker entered.")
        return

    try:
        quote = operations.look_up(user_id, symbol)
    except ValidationError as e:
        print(e)
        return

    print(f"{quote.company_name} ({quote.symbol}) is trading at ${quote.price:,.2f}")
    print(
        f"You have ${quote.cash:,.2f} in cash — enough for "
        f"{format_shares(quote.affordable)} shares."
    )

    already_held = quote.owned is not None
    if already_held:
        print(
            f"You already hold {format_shares(quote.owned)} shares "
            f"at ${quote.average_cost:,.2f} average cost."
        )

    if quote.affordable <= 0:
        print("You can't afford even one share right now. Sell something first.")
        return

    shares = ask_decimal(
        f"How many shares of {quote.symbol}? (max {format_shares(quote.affordable)}): ",
        maximum=quote.affordable,
        max_label=f"${quote.cash:,.2f} in cash",
        allow_all=True,
    )
    if shares is None:
        print("Nothing was added.")
        return

    total = shares * quote.price
    if not confirm(
        f"Buy {format_shares(shares)} shares of {quote.symbol} for ${total:,.2f}?"
    ):
        print("Nothing was added.")
        return

    try:
        purchase = operations.buy(user_id, quote.symbol, shares)
    except InsufficientFunds as e:
        print(e)
        return
    except ValidationError as e:
        print(e)
        return

    print(
        f"Added {format_shares(purchase.shares)} shares of "
        f"{purchase.symbol} at ${purchase.price:,.2f}."
    )
    if already_held:
        print(
            f"You now hold {format_shares(purchase.total_shares)} shares "
            f"at ${purchase.average_cost:,.2f} average cost."
        )
    print(f"Cash remaining: ${purchase.cash:,.2f}")


def sell_stock(user_id):
    symbol = prompt("Ticker to sell: ").upper()
    if not symbol:
        print("No ticker entered.")
        return

    holding = portfolio.get_holding(user_id, symbol)
    if holding is None:
        print(f"You don't hold any {symbol}.")
        return

    owned_shares, cost_basis = holding

    try:
        price = market_data.get_live_price(symbol)
    except Exception as e:
        print(f"Could not look up {symbol}: {e}")
        return

    if price is None:
        print(f"Could not get a current price for {symbol}. Sale cancelled.")
        return

    print(
        f"You hold {format_shares(owned_shares)} shares of {symbol}, "
        f"bought at ${cost_basis:,.2f}, now ${price:,.2f} each."
    )

    max_display = format_shares(operations.sellable_maximum(owned_shares))

    shares_to_sell = ask_decimal(
        f"How many shares to sell? (max {max_display}, 'a' for all, 'c' to cancel): ",
        maximum=owned_shares,
        max_label=f"{max_display} shares",
        allow_all=True,
    )
    if shares_to_sell is None:
        print("Sale cancelled.")
        return

    proceeds = shares_to_sell * price
    gain = (price - cost_basis) * shares_to_sell
    if not confirm(
        f"Sell {format_shares(shares_to_sell)} shares of {symbol} "
        f"for ${proceeds:,.2f} (realising ${gain:+,.2f})?"
    ):
        print("Sale cancelled.")
        return

    try:
        sale = operations.sell(user_id, symbol, shares_to_sell)
    except ValidationError:
        print("Sale failed — your position may have changed. Nothing was sold.")
        return

    print(
        f"Sold {format_shares(sale.shares)} shares of "
        f"{sale.symbol} for ${sale.proceeds:,.2f}."
    )
    if sale.closed:
        print(f"Position in {sale.symbol} closed.")
    else:
        print(f"You still hold {format_shares(sale.remaining)} shares.")
    print(f"Cash available: ${sale.cash:,.2f}")


MENU_ACTIONS = {
    "1": ("Update live prices", update_live_prices),
    "2": ("Load historical data", load_historical_data),
    "3": ("View portfolio report", print_full_report),
    "4": ("Add a stock", add_stock),
    "5": ("Sell a stock", sell_stock),
    "6": ("View account balance", show_balance),
}
EXIT_KEY = str(len(MENU_ACTIONS) + 1)


def run():
    try:
        account = users.resolve_cli_user()
    except UnknownUser as e:
        print("=== Portfolio Tracker ===\n")
        print(e)
        return

    user_id, _, email, display_name, _ = account
    who = display_name or email

    while True:
        print(f"\n=== Portfolio Tracker — {who} ===")
        for key, (label, _) in MENU_ACTIONS.items():
            print(f"{key}. {label}")
        print(f"{EXIT_KEY}. Exit")

        try:
            choice = prompt("Choose an option: ")
        except Cancelled:
            print("Goodbye.")
            return

        if choice.lower() in (EXIT_KEY, "exit", "quit", "q"):
            print("Goodbye.")
            return

        action = MENU_ACTIONS.get(choice)
        if action is None:
            print("Invalid option, try again.")
            continue

        print()
        try:
            action[1](user_id)
        except Cancelled:
            print("Cancelled.")
        except Exception as e:
            print(f"Something went wrong: {e}")

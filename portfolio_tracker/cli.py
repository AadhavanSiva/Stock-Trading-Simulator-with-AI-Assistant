from portfolio_tracker.models import portfolio, stocks, history
from portfolio_tracker.services import market_data
from portfolio_tracker.reports import print_full_report


def update_live_prices():
    symbols = portfolio.get_portfolio_symbols()
    if not symbols:
        print("No holdings yet — nothing to update.")
        return

    for symbol in symbols:
        try:
            price = market_data.get_live_price(symbol)
            if price is None:
                print(f"Skipping {symbol}: no price data available")
                continue
            stocks.update_stock_price(symbol, price)
            print(f"{symbol}: ${price}")
        except Exception as e:
            print(f"Error updating {symbol}: {e}")
            continue

    print("Price update complete.")


def load_historical_data():
    symbols = portfolio.get_portfolio_symbols()
    if not symbols:
        print("No holdings yet — nothing to load.")
        return

    for symbol in symbols:
        try:
            print(f"Pulling history for {symbol}...")
            count = history.load_history_for_symbol(symbol)
            if count == 0:
                print(f"No history found for {symbol}")
            else:
                print(f"Processed {count} rows for {symbol}")
        except Exception as e:
            print(f"Error loading history for {symbol}: {e}")
            continue

    print("Historical data load complete.")


def add_stock():
    symbol = input("Ticker to add (e.g. AAPL): ").strip().upper()
    if not symbol:
        print("No ticker entered.")
        return

    try:
        price = market_data.get_live_price(symbol)
    except Exception as e:
        print(f"Could not look up {symbol}: {e}")
        return

    if price is None:
        print(f"Could not find market data for '{symbol}'. Not added.")
        return

    try:
        shares = float(input(f"How many shares of {symbol}? "))
        purchase_price = float(input(f"Purchase price per share? "))
    except ValueError:
        print("Shares and price must be numbers. Nothing was added.")
        return

    company_name = market_data.get_company_name(symbol)
    stocks.upsert_stock(symbol, company_name, price)
    portfolio.add_holding(symbol, shares, purchase_price)
    print(f"Added {shares} shares of {symbol}.")


def remove_stock():
    symbol = input("Ticker to remove: ").strip().upper()
    if not symbol:
        print("No ticker entered.")
        return

    if portfolio.remove_holding(symbol):
        print(f"Removed {symbol} from your portfolio.")
    else:
        print(f"'{symbol}' was not found in your portfolio.")


MENU_ACTIONS = {
    "1": ("Update live prices", update_live_prices),
    "2": ("Load historical data", load_historical_data),
    "3": ("View portfolio report", print_full_report),
    "4": ("Add a stock", add_stock),
    "5": ("Remove a stock", remove_stock),
}


def run():
    while True:
        print("\n=== Portfolio Tracker ===")
        for key, (label, _) in MENU_ACTIONS.items():
            print(f"{key}. {label}")
        print("6. Exit")

        choice = input("Choose an option: ").strip()

        if choice == "6":
            print("Goodbye.")
            break

        action = MENU_ACTIONS.get(choice)
        if action is None:
            print("Invalid option, try again.")
            continue

        try:
            action[1]()
        except Exception as e:
            print(f"Something went wrong: {e}")
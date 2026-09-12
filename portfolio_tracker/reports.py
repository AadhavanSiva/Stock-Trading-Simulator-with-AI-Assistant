from portfolio_tracker.models.portfolio import get_holdings_with_prices
from portfolio_tracker.models.history import get_recent_averages, get_high_low


def print_portfolio_summary():
    holdings = get_holdings_with_prices()

    print("=" * 60)
    print("PORTFOLIO SUMMARY REPORT")
    print("=" * 60)

    if not holdings:
        print("\nNo holdings yet. Add a stock to get started.")
        return

    print(f"\n{'Symbol':<8}{'Shares':<10}{'Bought At':<12}{'Current':<12}{'Gain/Loss':<12}")
    print("-" * 54)

    total_value = 0
    total_gain_loss = 0
    for symbol, shares, purchase_price, current_price, gain_loss in holdings:
        total_value += shares * current_price
        total_gain_loss += gain_loss
        print(
            f"{symbol:<8}{shares:<10}"
            f"${purchase_price:<11.2f}${current_price:<11.2f}${gain_loss:<11.2f}"
        )

    print("-" * 54)
    print(f"Total Portfolio Value: ${total_value:.2f}")
    print(f"Total Gain/Loss: ${total_gain_loss:.2f}")


def print_history_summary(days=30):
    averages = get_recent_averages(days)
    high_low = get_high_low()

    if not averages and not high_low:
        print("\nNo historical data yet. Load history to see trends.")
        return

    if averages:
        print(f"\n{days}-Day Averages")
        print("-" * 30)
        for symbol, avg_close in averages:
            print(f"{symbol:<8}${avg_close}")

    if high_low:
        print("\nHistorical High/Low")
        print("-" * 30)
        for symbol, high, low in high_low:
            print(f"{symbol:<8}High: ${high:<10.2f}Low: ${low:<10.2f}")


def print_full_report(days=30):
    print_portfolio_summary()
    print_history_summary(days)
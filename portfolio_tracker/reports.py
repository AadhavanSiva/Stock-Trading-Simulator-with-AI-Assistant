from decimal import Decimal

from portfolio_tracker.models.history import get_recent_averages, get_high_low
from portfolio_tracker.operations import account_summary, format_shares

WIDTH = 78

_shares = format_shares


def print_balance(user_id, summary=None):
    """Cash, holdings, and what the two add up to."""
    summary = summary or account_summary(user_id)

    print("=" * WIDTH)
    print("ACCOUNT BALANCE")
    print("=" * WIDTH)
    print()
    print(f"{'Cash available':<28}{'$' + format(summary.cash, ',.2f'):>18}")
    print(f"{'Value of investments':<28}{'$' + format(summary.holdings_value, ',.2f'):>18}")
    print("-" * 46)
    print(f"{'Total account value':<28}{'$' + format(summary.net_worth, ',.2f'):>18}")


def print_portfolio_summary(user_id):
    summary = account_summary(user_id)
    holdings = summary.rows

    print("=" * WIDTH)
    print("PORTFOLIO SUMMARY REPORT")
    print("=" * WIDTH)

    if not holdings:
        print("\nNo holdings yet. Add a stock to get started.")
        print(f"\nCash available: ${summary.cash:,.2f}")
        return

    print(
        f"\n{'Symbol':<8}{'Shares':>12}{'Bought At':>12}"
        f"{'Current':>12}{'Value':>14}{'Gain/Loss':>20}"
    )
    print("-" * WIDTH)

    for row in holdings:
        # current_price is NULL until the first price update. Printing the
        # row anyway beats crashing the whole report on a None multiply.
        if row["current_price"] is None:
            print(
                f"{row['symbol']:<8}{_shares(row['shares']):>12}"
                f"{row['purchase_price']:>12,.2f}"
                f"{'—':>12}{'—':>14}{'no price yet':>20}"
            )
            continue

        pct = row["percent"]
        # A tiny cost basis can produce a four-digit percentage; drop the
        # decimal there so the column still lines up.
        pct_text = f"{pct:+.0f}%" if abs(pct) >= 1000 else f"{pct:+.1f}%"
        movement = f"{row['gain_loss']:+,.2f} ({pct_text})"
        print(
            f"{row['symbol']:<8}{_shares(row['shares']):>12}"
            f"{row['purchase_price']:>12,.2f}"
            f"{row['current_price']:>12,.2f}{row['value']:>14,.2f}{movement:>20}"
        )

    print("-" * WIDTH)
    print(f"{'Cost basis':<20}{'$' + format(summary.total_cost, ',.2f'):>18}")
    print(f"{'Market value':<20}{'$' + format(summary.holdings_value, ',.2f'):>18}")
    print(
        f"{'Total gain/loss':<20}{'$' + format(summary.total_gain, '+,.2f'):>18}"
        f"   ({summary.total_percent:+.2f}%)"
    )
    print()
    print(f"{'Cash available':<20}{'$' + format(summary.cash, ',.2f'):>18}")
    print(f"{'Total account value':<20}{'$' + format(summary.net_worth, ',.2f'):>18}")

    if summary.unpriced:
        print(
            f"\nNot counted above (no price yet): {', '.join(summary.unpriced)}"
            "\nRun 'Update live prices' to fill them in."
        )


def print_history_summary(days=30):
    averages = get_recent_averages(days)
    high_low = get_high_low()

    if not averages and not high_low:
        print("\nNo historical data yet. Load history to see trends.")
        return

    if averages:
        print(f"\n{days}-Day Averages")
        print("-" * 34)
        for symbol, avg_close in averages:
            print(f"{symbol:<8}{'$' + format(avg_close, ',.2f'):>12}")

    if high_low:
        print("\nHistorical High/Low")
        print("-" * 34)
        for symbol, high, low in high_low:
            print(
                f"{symbol:<8}"
                f"High {'$' + format(high, ',.2f'):>10}   "
                f"Low {'$' + format(low, ',.2f'):>10}"
            )


def print_full_report(user_id, days=30):
    print_portfolio_summary(user_id)
    print_history_summary(days)

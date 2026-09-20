"""Domain errors shared across layers.

These live in their own module so models can raise them without importing
the operations layer that sits above them.
"""


class ValidationError(ValueError):
    """Input that a person can correct, phrased for a person to read."""


class InsufficientFunds(ValidationError):
    """A purchase costs more than the account's available cash."""

    def __init__(self, needed, available):
        self.needed = needed
        self.available = available
        super().__init__(
            f"That costs ${needed:,.2f} but you only have ${available:,.2f} "
            f"in cash. Sell something or buy fewer shares."
        )


class UnknownUser(ValidationError):
    """No account matched the identifier we were given."""


class MarketDataUnavailable(Exception):
    """The market data provider could not be reached, timed out, or failed.

    The message is written for a person and never includes the underlying
    library or network error, which is logged where it happens instead.
    """

    def __init__(self, symbol):
        self.symbol = symbol
        super().__init__(
            f"Could not look up {symbol} right now. The market data service "
            f"didn't respond, so try again in a minute."
        )


class MarketDataCredentialsRejected(MarketDataUnavailable):
    """The provider refused our API credentials, or the data plan.

    A subclass, so every existing caller still catches it and a reader
    still sees "try again in a minute" — retrying is the only thing they
    can usefully do. It is separate so the *logs* can say plainly that
    this one will not fix itself, because it is a misconfiguration rather
    than an outage.
    """


class UnknownSymbol(ValidationError):
    """The provider has no such ticker.

    A ValidationError, because it is a thing the person can correct by
    typing a different symbol — unlike an outage, which they cannot.
    """

    def __init__(self, symbol):
        self.symbol = symbol
        super().__init__(
            f"No market data found for '{symbol}'. Check the ticker and try again."
        )

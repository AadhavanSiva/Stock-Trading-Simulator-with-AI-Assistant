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
    """Yahoo Finance could not be reached, timed out, or failed.

    The message is written for a person and never includes the underlying
    library or network error, which is logged where it happens instead.
    """

    def __init__(self, symbol):
        self.symbol = symbol
        super().__init__(
            f"Could not look up {symbol} right now. Yahoo Finance didn't "
            f"respond, so try again in a minute."
        )

"""Background price refreshes triggered by reads."""
import logging

from portfolio_tracker import operations

log = logging.getLogger(__name__)


def freshen(user_id, symbols):
    """Start a background refresh if needed, and say how old prices are.

    Never blocks: the response carries what is stored, stamped with when
    it was stored, and the next read picks up whatever the refresh wrote.
    """
    symbols = list(symbols)
    if not symbols:
        return None
    try:
        operations.ensure_prices_fresh(user_id, symbols)
    except Exception:
        # A refresh that cannot even be started must not cost a response.
        log.warning("Could not start a background price refresh", exc_info=True)
    return operations.prices_as_of(symbols)

"""Turn a price series into the numbers an SVG needs.

Pure arithmetic: in go (label, value) pairs, out come coordinates in a
viewBox. Nothing here touches the database, the network, or a template —
which is what makes it straightforward to test that a flat series does not
divide by zero and a single point does not produce a broken path.

The chart is drawn server-side as inline SVG, so it needs no charting
library, no build step, and works with JavaScript switched off.
"""
from collections import namedtuple
from decimal import ROUND_HALF_UP, Decimal

Chart = namedtuple(
    "Chart",
    "points path area_path width height low high average first last change "
    "change_pct baseline_y count plotted",
)

# A 720-unit chart cannot show more distinct x positions than it has units.
# Beyond this, extra points are invisible but still shipped in the HTML —
# the all-time AAPL series is 11,529 points and ~367 KB unreduced.
MAX_PLOTTED = 720

RANGES = {
    "1d":  {"label": "1 day",    "days": 1,    "intraday": True},
    "5d":  {"label": "5 days",   "days": 5,    "intraday": True},
    "1m":  {"label": "1 month",  "days": 30,   "intraday": False},
    "3m":  {"label": "3 months", "days": 90,   "intraday": False},
    "6m":  {"label": "6 months", "days": 182,  "intraday": False},
    "1y":  {"label": "1 year",   "days": 365,  "intraday": False},
    "all": {"label": "All time", "days": None, "intraday": False},
}
DEFAULT_RANGE = "6m"


def normalise_range(value):
    """Map a query parameter to a known range key, defaulting quietly."""
    key = (value or "").strip().lower()
    return key if key in RANGES else DEFAULT_RANGE


def _reduce(series, limit):
    """Thin a long series for drawing without flattening its shape.

    Each bucket keeps its lowest and highest point, in time order, so a
    sharp dip or spike survives even when most points are dropped. The
    true first and last points are always kept, because the chart's
    start and end are what the headline figures describe.
    """
    if len(series) <= limit:
        return series

    inner = series[1:-1]
    buckets = max(1, (limit - 2) // 2)
    size = len(inner) / buckets
    reduced = [series[0]]
    for b in range(buckets):
        chunk = inner[int(b * size):int((b + 1) * size)]
        if not chunk:
            continue
        lo = min(range(len(chunk)), key=lambda i: chunk[i][1])
        hi = max(range(len(chunk)), key=lambda i: chunk[i][1])
        for i in sorted({lo, hi}):
            reduced.append(chunk[i])
    reduced.append(series[-1])
    return reduced


def build(series, width=720, height=220, pad=6, max_points=MAX_PLOTTED):
    """Project (label, value) pairs onto a viewBox.

    Returns None for an empty series so callers can render an explanation
    instead of an empty frame.

    Two edge cases matter more than they look:
      - a flat series makes high == low, and naive scaling divides by zero
      - a single point has no line to draw, so it gets a path that is still
        valid SVG rather than "M" followed by nothing
    """
    series = [(label, Decimal(value)) for label, value in series if value is not None]
    if not series:
        return None

    # Every figure comes from the FULL series, before any thinning, so the
    # numbers are exact even when the line is drawn from fewer points.
    values = [value for _, value in series]
    low, high = min(values), max(values)
    first, last = values[0], values[-1]
    # HALF_UP to match to_money() everywhere else. quantize() alone would
    # use the context default, banker's rounding, turning 15.005 into 15.00
    # here while the same figure rounds to 15.01 on every other page.
    average = (sum(values) / len(values)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = len(series)

    series = _reduce(series, max_points)

    usable = height - (pad * 2)
    span = high - low

    def y_for(value):
        if span == 0:
            # A perfectly flat line sits in the middle rather than at the
            # top, which is where (value - low) / 0 would put it.
            return height / 2
        # SVG y grows downward, so the tallest value gets the smallest y.
        return pad + float((high - value) / span) * usable

    count = len(series)
    if count == 1:
        x_for = lambda i: width / 2
    else:
        x_for = lambda i: (i / (count - 1)) * width

    points = [
        {"x": round(x_for(i), 2), "y": round(y_for(value), 2),
         "label": label, "value": value}
        for i, (label, value) in enumerate(series)
    ]

    if count == 1:
        # A dot needs a zero-length line, not a malformed one.
        path = f"M {points[0]['x']} {points[0]['y']} L {points[0]['x']} {points[0]['y']}"
    else:
        path = "M " + " L ".join(f"{p['x']} {p['y']}" for p in points)

    # The fill under the line, closed along the bottom edge.
    area_path = (
        path
        + f" L {points[-1]['x']} {height} L {points[0]['x']} {height} Z"
    )

    change = last - first
    change_pct = (change / first * 100) if first else Decimal(0)

    return Chart(
        points=points,
        path=path,
        area_path=area_path,
        width=width,
        height=height,
        low=low,
        high=high,
        average=average,
        first=first,
        last=last,
        change=change,
        change_pct=change_pct,
        baseline_y=round(y_for(first), 2),
        count=total,
        plotted=count,
    )

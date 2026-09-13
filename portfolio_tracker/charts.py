"""Turn a price series into the numbers an SVG chart and its axes need.

Pure arithmetic: in go (label, value) pairs, out come coordinates in a
viewBox plus tick marks for the price and date axes. Nothing here touches
the database, the network or a template, which is what makes the edge
cases — a flat series, a single point, a sub-cent price — easy to test.

The chart is drawn server-side as inline SVG, so it needs no charting
library, no build step, and works with JavaScript switched off.
"""
from collections import namedtuple
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

Chart = namedtuple(
    "Chart",
    "points path area_path width height low high average first last change "
    "change_pct baseline_y count plotted y_ticks x_ticks",
)

# Intraday timestamps are shown in exchange time, where a trading day
# starts and ends — not the viewer's or the server's local time.
EXCHANGE_TZ = ZoneInfo("America/New_York")

# A 720-unit chart cannot show more distinct x positions than it has units.
# Beyond this, extra points are invisible but still shipped in the HTML —
# the all-time AAPL series is 11,529 points and ~367 KB unreduced.
MAX_PLOTTED = 720

Y_TICK_TARGET = 5
X_TICK_TARGET = 5

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


# ------------------------------------------------------------------ price axis

_NICE_FRACTIONS = (Decimal(1), Decimal(2), Decimal("2.5"), Decimal(5), Decimal(10))


def _nice_step(raw):
    """Round a step up to 1, 2, 2.5 or 5 times a power of ten.

    So a $0–$340 range is ruled every $50, not every $68.
    """
    exponent = raw.adjusted()            # power of ten of the leading digit
    fraction = raw.scaleb(-exponent)     # now 1 <= fraction < 10
    for nice in _NICE_FRACTIONS:
        if fraction <= nice:
            return nice.scaleb(exponent)
    return Decimal(10).scaleb(exponent)


def price_axis(low, high, target=Y_TICK_TARGET):
    """Tick values that bracket [low, high] on round numbers.

    Returns (start, end, ticks). The chart is scaled to start..end rather
    than low..high, so every gridline lands exactly on a labelled price.
    """
    low, high = Decimal(low), Decimal(high)
    if high == low:
        # A flat line still needs a readable scale around it.
        spread = max(abs(high) * Decimal("0.02"), Decimal("0.01"))
        low, high = low - spread, high + spread

    step = _nice_step((high - low) / (target - 1))
    start = (low / step).to_integral_value(rounding=ROUND_FLOOR) * step
    end = (high / step).to_integral_value(rounding=ROUND_CEILING) * step
    start = max(start, Decimal(0))  # a share price cannot go below zero

    ticks = []
    value = start
    while value <= end:
        ticks.append(value)
        value += step
    return start, end, ticks


def price_label(value, step):
    """$300 for whole-dollar steps; $12.50 or $0.05 when the step needs cents."""
    step = step.normalize()
    places = -step.as_tuple().exponent
    decimals = 0 if places <= 0 else min(max(places, 2), 4)
    return f"${value:,.{decimals}f}"


# ------------------------------------------------------------------- date axis

def date_label(moment, window):
    """Format one x-axis position for the range being shown.

    strftime's no-padding flags differ between platforms ("%-d" fails on
    Windows), so day numbers and 12-hour times are assembled by hand.

    A label that is not a date is shown as-is rather than raising: a bad
    label should cost an axis caption, never the whole chart.
    """
    if not isinstance(moment, date):
        return str(moment)
    if isinstance(moment, datetime):
        if moment.tzinfo is not None:
            moment = moment.astimezone(EXCHANGE_TZ)
        if window == "1d":
            hour = moment.hour % 12 or 12
            suffix = "am" if moment.hour < 12 else "pm"
            return f"{hour}:{moment.minute:02d}{suffix}"
        if window == "5d":
            return f"{moment:%a} {moment.day}"

    if window == "all":
        return f"{moment:%Y}"
    if window == "1y":
        return f"{moment:%b} {moment:%Y}"
    return f"{moment:%b} {moment.day}"


def point_label(moment):
    """The full date (and, for intraday prices, time) of a single point.

    Axis labels are abbreviated to fit; a tooltip or a table row names the
    exact moment instead: "Sep 12, 2026" or "Fri Sep 12, 10:35am ET".
    """
    if not isinstance(moment, date):
        return str(moment)
    if isinstance(moment, datetime):
        if moment.tzinfo is not None:
            moment = moment.astimezone(EXCHANGE_TZ)
        hour = moment.hour % 12 or 12
        suffix = "am" if moment.hour < 12 else "pm"
        return f"{moment:%a} {moment:%b} {moment.day}, {hour}:{moment.minute:02d}{suffix} ET"
    return f"{moment:%b} {moment.day}, {moment:%Y}"


MAX_READOUTS = 360


def readouts(chart, limit=MAX_READOUTS):
    """Plotted points as [x%, y%, when, price] for the hover readout and table.

    Percentages rather than viewBox units, because the plot stretches to its
    container. Compact arrays, and at most `limit` of them: a pointer cannot
    pick between more positions than that on a page-width chart, and the
    all-time page has a size budget. The last point, the current price, is
    always kept.
    """
    if chart is None:
        return []
    points = chart.points
    if len(points) > limit:
        stride = len(points) / (limit - 1)
        picked = [points[int(i * stride)] for i in range(limit - 1)]
        points = picked + [points[-1]]
    return [
        [
            round(point["x"] / chart.width * 100, 2),
            round(point["y"] / chart.height * 100, 2),
            point_label(point["label"]),
            f"${Decimal(point['value']):,.2f}",
        ]
        for point in points
    ]


# ----------------------------------------------------------------------- build

def _reduce(series, limit):
    """Thin a long series for drawing without flattening its shape.

    Each bucket keeps its lowest and highest point, in time order, so a
    sharp dip or spike survives even when most points are dropped. The
    true first and last points are always kept, because the chart's start
    and end are what the headline figures describe.
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


def build(series, width=720, height=220, pad=6, max_points=MAX_PLOTTED, window=None):
    """Project (label, value) pairs onto a viewBox, with axis ticks.

    Returns None for an empty series so callers can render an explanation
    instead of an empty frame.

    Edge cases that matter more than they look:
      - a flat series makes high == low, which naive scaling divides by
      - a single point has no line to draw, but still needs valid SVG
      - a sub-dollar stock needs cents on its price labels
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
    count = len(series)

    axis_start, axis_end, tick_values = price_axis(low, high)
    axis_span = axis_end - axis_start
    usable = height - (pad * 2)

    def y_for(value):
        # SVG y grows downward, so the highest price gets the smallest y.
        return pad + float((axis_end - value) / axis_span) * usable

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
        # A dot needs a zero-length line, not "M" followed by nothing.
        path = f"M {points[0]['x']} {points[0]['y']} L {points[0]['x']} {points[0]['y']}"
    else:
        path = "M " + " L ".join(f"{p['x']} {p['y']}" for p in points)

    area_path = path + f" L {points[-1]['x']} {height} L {points[0]['x']} {height} Z"

    step = tick_values[1] - tick_values[0] if len(tick_values) > 1 else axis_span
    y_ticks = [
        {
            "value": value,
            "label": price_label(value, step),
            "y": round(y_for(value), 2),
            "pct": round(y_for(value) / height * 100, 3),
        }
        for value in reversed(tick_values)   # top of the axis first
    ]

    # "All time" labels by year, but a short history (a recent listing, or
    # only a partial download) would put every tick in the same year and
    # collapse the axis to a single "2026". Label short spans by month.
    label_window = window
    first_label, last_label = points[0]["label"], points[-1]["label"]
    if (window == "all" and isinstance(first_label, date)
            and isinstance(last_label, date)
            and (last_label - first_label).days < 730):
        label_window = "1y"

    x_ticks = []
    if count == 1:
        indexes = [0]
    else:
        wanted = min(X_TICK_TARGET, count)
        indexes = sorted({round(i * (count - 1) / (wanted - 1)) for i in range(wanted)}) \
            if wanted > 1 else [0]
    for index in indexes:
        point = points[index]
        label = date_label(point["label"], label_window)
        # Two ticks landing on the same day (common on 5D) would print the
        # same label twice; keep the first.
        if x_ticks and x_ticks[-1]["label"] == label:
            continue
        x_ticks.append({"label": label, "pct": round(point["x"] / width * 100, 3)})
    for tick in x_ticks:
        tick["edge"] = ("start" if tick["pct"] <= 0.5
                        else "end" if tick["pct"] >= 99.5 else "middle")

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
        y_ticks=y_ticks,
        x_ticks=x_ticks,
    )

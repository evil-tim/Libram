"""Moving average calculations (SMA and EMA)."""

from datetime import datetime


def _format_date(ts: datetime) -> str:
    """Format a timestamp as a YYYY-MM-DD date string for moving-average output."""
    if isinstance(ts, datetime):
        return ts.date().isoformat()
    # fall back to the first 10 chars of any date-like string
    return str(ts)[:10]


def compute_sma(series: list[tuple[datetime, float]], period: int) -> list[dict[str, object]]:
    """Compute the Simple Moving Average over a (timestamp, value) series.

    Returns a list of {"date": str, "value": float} entries. The first `period - 1`
    data points have no entry (not enough values to fill the window).
    """
    out: list[dict[str, object]] = []
    n = len(series)
    window_sum = 0.0
    for i in range(n):
        window_sum += series[i][1]
        if i >= period:
            window_sum -= series[i - period][1]
        if i >= period - 1:
            out.append({
                "date": _format_date(series[i][0]),
                "value": round(window_sum / period, 4),
            })
    return out


def compute_ema(series: list[tuple[datetime, float]], period: int) -> list[dict[str, object]]:
    """Compute the Exponential Moving Average over a (timestamp, value) series.

    Seeds the first EMA value with the SMA of the first `period` data points,
    then recurses: ema_today = value_today * k + ema_yesterday * (1 - k),
    where k = 2 / (period + 1). The first `period - 1` data points have no entry.
    """
    out: list[dict[str, object]] = []
    n = len(series)
    if n < period:
        return out

    k = 2.0 / (period + 1)
    seed_sum = 0.0
    for i in range(period):
        seed_sum += series[i][1]
    prev_ema = seed_sum / period
    out.append({
        "date": _format_date(series[period - 1][0]),
        "value": round(prev_ema, 4),
    })

    for i in range(period, n):
        value = series[i][1]
        prev_ema = value * k + prev_ema * (1 - k)
        out.append({
            "date": _format_date(series[i][0]),
            "value": round(prev_ema, 4),
        })
    return out

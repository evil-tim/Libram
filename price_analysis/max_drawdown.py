"""Maximum drawdown calculation."""

from datetime import datetime


def compute_max_drawdown(series: list[tuple[datetime, float]]) -> float:
    """Largest peak-to-trough decline over a (timestamp, value) series.

    Returns the max drawdown as a negative percentage (e.g., -8.1).
    O(n) algorithm that tracks the running peak and computes the
    drawdown at each point.
    """
    if not series:
        return 0.0

    peak = series[0][1]
    max_dd = 0.0

    for _, close in series:
        if close > peak:
            peak = close
        if peak > 0:
            dd = (peak - close) / peak
            if dd > max_dd:
                max_dd = dd

    return round(-max_dd * 100, 2)

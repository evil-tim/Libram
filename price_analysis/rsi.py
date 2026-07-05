"""Relative Strength Index (RSI) calculation."""

from datetime import datetime

from price_analysis.date_utils import _format_date


def compute_rsi(series: list[tuple[datetime, float]], period: int) -> list[dict[str, object]]:
    """Compute the Relative Strength Index over a (timestamp, value) series.

    Uses Wilder's exponential smoothing for the average gain/loss after the
    initial seed.  Returns a list of {"date": str, "value": float} entries.
    Requires at least `period + 1` data points to produce the first value.
    """
    out: list[dict[str, object]] = []

    # Need at least period + 1 price points to get `period` deltas
    if len(series) < period + 1:
        return out

    # --- compute deltas ---
    deltas: list[float] = []
    for i in range(1, len(series)):
        deltas.append(series[i][1] - series[i - 1][1])

    # --- seed: simple average of the first `period` deltas ---
    seed_gains = 0.0
    seed_losses = 0.0
    for d in deltas[:period]:
        if d > 0:
            seed_gains += d
        else:
            seed_losses += abs(d)

    avg_gain = seed_gains / period
    avg_loss = seed_losses / period

    # first RSI value corresponds to series[period] (after `period` deltas)
    def _rsi(ag: float, al: float) -> float:
        if al == 0.0:
            return 100.0
        if ag == 0.0:
            return 0.0
        rs = ag / al
        return round(100.0 - 100.0 / (1.0 + rs), 4)

    out.append({
        "date": _format_date(series[period][0]),
        "value": _rsi(avg_gain, avg_loss),
    })

    # --- Wilder's exponential smoothing for remaining deltas ---
    for i in range(period, len(deltas)):
        d = deltas[i]
        current_gain = d if d > 0 else 0.0
        current_loss = abs(d) if d < 0 else 0.0

        avg_gain = (avg_gain * (period - 1) + current_gain) / period
        avg_loss = (avg_loss * (period - 1) + current_loss) / period

        out.append({
            "date": _format_date(series[i + 1][0]),
            "value": _rsi(avg_gain, avg_loss),
        })

    return out

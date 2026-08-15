"""Price analysis utilities for moving averages, RSI, max drawdown, and date handling.

This package exposes a compact public API. Private helpers (prefixed with ``_``)
are aliased to public names for discoverability.
"""

from .comparison import build_comparison_payload
from .date_utils import convert_to_timezone_aware
from .max_drawdown import compute_max_drawdown
from .moving_averages import compute_ema, compute_sma
from .rsi import compute_rsi

__all__ = [
    "build_comparison_payload",
    "compute_ema",
    "compute_max_drawdown",
    "compute_rsi",
    "compute_sma",
    "convert_to_timezone_aware",
]

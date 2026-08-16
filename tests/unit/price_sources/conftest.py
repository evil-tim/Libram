"""Test-package compatibility for the absent optional Coingecko datasource."""

import os
import sys
import time
import types
from pathlib import Path

# Several datasource parsers convert epoch values through the process-local
# timezone. Keep those expectations stable on developer and CI machines.
os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()


if not (
    Path(__file__).parents[3] / "price_sources" / "coingecko_ohlc_datasource.py"
).exists():
    module = types.ModuleType("price_sources.coingecko_ohlc_datasource")
    module.CoingeckoOHLCDataSource = type("CoingeckoOHLCDataSource", (), {})
    sys.modules[module.__name__] = module

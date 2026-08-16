"""Shared setup for price-source tests."""

import os
import time

# Several datasource parsers convert epoch values through the process-local
# timezone. Keep those expectations stable on developer and CI machines.
os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()

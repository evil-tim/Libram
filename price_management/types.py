from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

@dataclass
class EntityRecord:
    code: Optional[str] = None
    name: Optional[str] = None
    currency: Optional[str] = None
    datasource: Optional[str] = None
    config: Optional[dict[str, object]] = None
    type: Optional[str] = None
    frequency: Optional[str] = None
    has_weekend: bool = False
    timezone: Optional[str] = None
    min_timestamp: Optional[datetime] = None

@dataclass
class PriceRecord:
    # single price point
    price: Optional[Decimal] = None
    timestamp: Optional[datetime] = None
    # or OHLC bars
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None
    timestamp_start: Optional[datetime] = None
    timestamp_end: Optional[datetime] = None

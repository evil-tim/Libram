from __future__ import annotations

from pydantic import BaseModel

ALLOWED_FUNDAMENTAL_METRICS = {
    "market_cap",
    "pe_ratio",
    "pb_ratio",
    "eps",
    "shares_outstanding",
    "dividend_yield",
    "net_income_ttm",
}

VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}


class FundamentalsNotFound(Exception):
    pass


class FundamentalsValidationError(Exception):
    pass


class   FundamentalsRequest(BaseModel):
    entity_code: str
    metrics: dict[str, float]
    source_name: str
    source_url: str = ""
    as_of_date: str = ""
    confidence: str = "medium"
    notes: str = ""

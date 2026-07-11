from __future__ import annotations

from pydantic import BaseModel, Field

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


class FundamentalsRequest(BaseModel):
    entity_code: str = Field(..., description="Entity code / ticker (e.g. XYZ)")
    metrics: dict[str, float] = Field(..., description="Dictionary of fundamental metrics and their values. Allowed keys: " + ", ".join(sorted(ALLOWED_FUNDAMENTAL_METRICS)))
    source_name: str = Field(..., description="Name of the data source or provider")
    source_url: str = Field("", description="Optional URL pointing to the source or provenance")
    as_of_date: str = Field("", description="Snapshot date in ISO 8601 (e.g. 2025-12-31T00:00:00)")
    confidence: str = Field("medium", description="Confidence level for these values. Valid values: high, medium, low")
    notes: str = Field("", description="Freeform notes. Optional field for additional context or information about the data.")

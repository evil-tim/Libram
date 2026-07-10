from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel

from price_management.client import PriceManagerClient


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
    entity_code: str
    metrics: dict[str, float]
    source_name: str
    source_url: str = ""
    as_of_date: str = ""
    confidence: str = "medium"
    notes: str = ""


def _format_fundamentals_response(row: dict, entity: Optional[dict]) -> dict:
    metrics_raw = row.get("metrics", {})
    metrics = {k: metrics_raw.get(k) for k in ALLOWED_FUNDAMENTAL_METRICS}

    uploaded_at = row.get("uploaded_at")
    as_of_date = row.get("as_of_date")

    return {
        "snapshot_id": row.get("id"),
        "entity_id": str(row.get("entity_id")),
        "entity_code": entity.get("code") if entity else None,
        "entity_name": entity.get("name") if entity else None,
        "metrics": metrics,
        "source": {
            "name": row.get("source_name"),
            "url": row.get("source_url", ""),
            "as_of_date": str(as_of_date) if as_of_date else None,
            "confidence": row.get("confidence"),
            "notes": row.get("notes", ""),
        },
        "uploaded_at": uploaded_at.isoformat() if uploaded_at is not None else None,
        "uploaded_by": row.get("uploaded_by", "agent"),
    }


def upload_fundamentals(body: FundamentalsRequest, price_manager: PriceManagerClient) -> dict:
    entities = price_manager.query_entities(None, body.entity_code, None, None)
    entities_list = list(entities)
    if not entities_list:
        raise FundamentalsNotFound(f"entity not found: {body.entity_code}")
    entity = entities_list[0]
    entity_id = entity.id

    unknown_keys = set(body.metrics.keys()) - ALLOWED_FUNDAMENTAL_METRICS
    if unknown_keys:
        raise FundamentalsValidationError(
            f"unknown metric keys: {sorted(unknown_keys)}. allowed: {sorted(ALLOWED_FUNDAMENTAL_METRICS)}"
        )

    for key, value in body.metrics.items():
        if value is None:
            raise FundamentalsValidationError(f"metric '{key}' has null value; omit the key instead")

    if body.confidence not in VALID_CONFIDENCE_LEVELS:
        raise FundamentalsValidationError(
            f"invalid confidence: '{body.confidence}'. must be one of: {sorted(VALID_CONFIDENCE_LEVELS)}"
        )

    as_of_date = body.as_of_date if body.as_of_date else str(date.today())

    row = price_manager.upload_fundamentals(
        entity_id=entity_id,
        metrics=body.metrics,
        source_name=body.source_name,
        source_url=body.source_url,
        as_of_date=as_of_date,
        confidence=body.confidence,
        notes=body.notes,
    )

    entity_raw = price_manager.db.get_entity_by_id_raw(entity_id)
    return _format_fundamentals_response(row, entity_raw)


def fetch_entity_fundamentals(entity_code: str, latest_only: bool, price_manager: PriceManagerClient) -> list[dict]:
    entity_raw, rows = price_manager.get_fundamentals(entity_code, latest_only=latest_only)
    if entity_raw is None:
        raise FundamentalsNotFound(f"entity not found: {entity_code}")

    return [_format_fundamentals_response(row, entity_raw) for row in rows]

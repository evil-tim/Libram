from datetime import datetime
from typing import Optional

from fundamentals_management import ALLOWED_FUNDAMENTAL_METRICS, VALID_CONFIDENCE_LEVELS, FundamentalsNotFound, FundamentalsValidationError, FundamentalsRequest
from libram_database.db import Database
from price_management.client import PriceManagerClient


class FundamentalsManagerClient:
    """High-level client to fetch/store/query entity fundamentals data.
    """

    def __init__(self, price_manager_client: PriceManagerClient, db: Database):
        self.price_manager_client = price_manager_client
        self.db = db


    def _format_fundamentals_response(self, row: dict, entity: Optional[dict]) -> dict:
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


    def upload_fundamentals(self, body: FundamentalsRequest) -> dict:
        entities = self.price_manager_client.query_entities(None, body.entity_code, None, None)
        entities_list = list(entities)
        if not entities_list:
            raise FundamentalsNotFound(f"entity not found: {body.entity_code}")
        entity = entities_list[0]
        entity_id = entity.id

        # if any of the metrics keys are not in the allowed set, raise an error
        unknown_keys = set(body.metrics.keys()) - ALLOWED_FUNDAMENTAL_METRICS
        if unknown_keys:
            raise FundamentalsValidationError(
                f"unknown metric keys: {sorted(unknown_keys)}. allowed: {sorted(ALLOWED_FUNDAMENTAL_METRICS)}"
            )

        # if any of the metrics values are None, raise an error
        for key, value in body.metrics.items():
            if value is None:
                raise FundamentalsValidationError(f"metric '{key}' has null value; omit the key instead")

        # if none of the metric keys provided are in the allowed set, raise an error
        if not any(key in ALLOWED_FUNDAMENTAL_METRICS for key in body.metrics.keys()):
            raise FundamentalsValidationError(
                f"no valid metric keys provided. allowed: {sorted(ALLOWED_FUNDAMENTAL_METRICS)}"
            )

        # if the confidence level is not in the allowed set, raise an error
        if body.confidence not in VALID_CONFIDENCE_LEVELS:
            raise FundamentalsValidationError(
                f"invalid confidence: '{body.confidence}'. must be one of: {sorted(VALID_CONFIDENCE_LEVELS)}"
            )

        # if as_of_date is provided, validate that it is a valid ISO 8601 date
        as_of_date = None
        if body.as_of_date:
            try:
                as_of_date = datetime.fromisoformat(body.as_of_date)
            except ValueError:
                raise FundamentalsValidationError(
                    f"invalid as_of_date: '{body.as_of_date}'. must be a valid ISO 8601 date (e.g. 2025-12-31T00:00:00)"
                )
        else:
            as_of_date = datetime.today()


        row = self.db.insert_fundamentals(
            entity_id=entity_id,
            metrics=body.metrics,
            source_name=body.source_name,
            source_url=body.source_url,
            as_of_date=as_of_date,
            confidence=body.confidence,
            notes=body.notes,
            uploaded_by="agent",
        )

        entity_raw = self.db.get_entity_by_id_raw(entity_id)
        return self._format_fundamentals_response(row, entity_raw)


    def fetch_entity_fundamentals(self, entity_code: str, latest_only: bool) -> list[dict]:
        entities = self.price_manager_client.query_entities(None, entity_code, None, None)

        entities_list = list(entities)
        if not entities_list:
            raise FundamentalsNotFound(f"entity not found: {entity_code}")

        entity = entities_list[0]
        entity_id = entity.id
        rows = self.db.get_fundamentals_by_entity(entity_id, latest_only=latest_only)
        # Return the raw entity dict from DB for richer data (name, etc.)
        entity_raw = self.db.get_entity_by_id_raw(entity_id)
        if entity_raw is None:
            raise FundamentalsNotFound(f"entity not found: {entity_code}")

        return [self._format_fundamentals_response(row, entity_raw) for row in rows]

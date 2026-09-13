from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from currency_conversion.conversion import InvalidRate, NoPath, NoRate
from currency_conversion.price_records import (
    convert_price_records,
    parse_quote_currency,
)
from currency_conversion.service import CurrencyConversionService
from dependencies import get_currency_conversion_service, get_price_manager_service
from price_analysis import convert_to_timezone_aware
from price_management.service import PriceManagerService

router = APIRouter()


def _as_uuid(value: object) -> UUID | None:
    """Coerce a stored currency id to UUID, matching the database layer's types."""
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))


@router.get(
    "/api/v1/prices",
    operation_id="list_prices_for_entity",
    description="List price records for an entity within a date range ordered by date ascending. Can be single price at timestamp or OHLC within date range, depending on the entity. Supports pagination with page and size query parameters.",
)
async def list_prices(
    entity_id: Annotated[UUID, Query(description="Select by entity UUID")],
    start: Annotated[
        str,
        Query(
            description="Start date for the date range, inclusive. Automatically converted to the entity's timezone. Format: YYYY-MM-DDTHH:MM:SS"
        ),
    ],
    end: Annotated[
        str,
        Query(
            description="End date for the date range, exclusive. Automatically converted to the entity's timezone. Format: YYYY-MM-DDTHH:MM:SS"
        ),
    ],
    page: Annotated[
        int, Query(description="Page number for pagination, zero-indexed, default is 0")
    ] = 0,
    size: Annotated[
        int, Query(description="Number of items per page, default is 10")
    ] = 10,
    quote_currency_id: Annotated[
        str | None,
        Query(
            description=(
                "Optional output currency. Pass a currency entity UUID, or 'PHP' for the "
                "entity-less peso. When set, every monetary field of every returned record "
                "is converted into that currency, and each record reports the conversion it "
                "underwent. Omitted returns the records unconverted."
            )
        ),
    ] = None,
    price_manager: PriceManagerService = Depends(get_price_manager_service),
    currency_conversion: CurrencyConversionService = Depends(
        get_currency_conversion_service
    ),
):
    entity = price_manager.db.get_entity_by_id_raw(entity_id)
    if not entity:
        raise ValueError("entity not found")
    timezone = entity.get("timezone")
    if not timezone or not isinstance(timezone, str):
        timezone = "UTC"

    start_dt = convert_to_timezone_aware(start, timezone)
    end_dt = convert_to_timezone_aware(end, timezone)

    records = price_manager.query_prices(entity_id, start_dt, end_dt, page, size)

    # Omitting the output currency leaves the response exactly as it was before
    # this parameter existed.
    if quote_currency_id is None:
        return records

    try:
        requested_currency_id = parse_quote_currency(quote_currency_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_value",
                "field": "quote_currency_id",
                "value": quote_currency_id,
            },
        ) from exc

    if requested_currency_id is not None and not currency_conversion.currency_exists(
        requested_currency_id
    ):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "entity_not_found",
                "entity_id": str(requested_currency_id),
            },
        )

    source_currency_id = _as_uuid(entity.get("currency_id"))
    try:
        return convert_price_records(
            records,
            source_currency_id,
            requested_currency_id,
            currency_conversion,
        )
    except NoPath as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "fx_no_path",
                "from": currency_conversion.currency_code(source_currency_id),
                "to": currency_conversion.currency_code(requested_currency_id),
            },
        ) from exc
    except InvalidRate as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "fx_invalid_rate", "reason": str(exc)},
        ) from exc
    except NoRate as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "fx_rate_unavailable", "reason": str(exc)},
        ) from exc


@router.get(
    "/api/v1/prices/summary",
    operation_id="list_price_summary",
    description="Return aggregate summary statistics (count, min, max, avg, std_dev, first_close, last_close, period_return_pct) for an entity's price series within a date range. Works for both OHLC and single-price entities.",
)
async def list_price_summary(
    entity_id: Annotated[UUID, Query(description="Select by entity UUID")],
    start: Annotated[
        str,
        Query(
            description="Start date for the date range, inclusive. Automatically converted to the entity's timezone. Format: YYYY-MM-DDTHH:MM:SS"
        ),
    ],
    end: Annotated[
        str,
        Query(
            description="End date for the date range, exclusive. Automatically converted to the entity's timezone. Format: YYYY-MM-DDTHH:MM:SS"
        ),
    ],
    price_manager: PriceManagerService = Depends(get_price_manager_service),
):
    entity = price_manager.db.get_entity_by_id_raw(entity_id)
    if not entity:
        raise ValueError("entity not found")
    timezone = entity.get("timezone")
    if not timezone or not isinstance(timezone, str):
        timezone = "UTC"

    start_dt = convert_to_timezone_aware(start, timezone)
    end_dt = convert_to_timezone_aware(end, timezone)

    summary = price_manager.query_price_summary(entity_id, start_dt, end_dt)
    if not summary:
        raise ValueError("no price data found for entity in the given date range")
    return {
        "entity_id": str(entity_id),
        "start": start,
        "end": end,
        **summary,
    }

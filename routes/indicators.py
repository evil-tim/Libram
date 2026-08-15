from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from price_management.service import PriceManagerService
from price_analysis import (
    compute_sma,
    compute_ema,
    compute_rsi,
    convert_to_timezone_aware,
)
from dependencies import get_price_manager_service

router = APIRouter()


@router.get(
    "/api/v1/prices/sma",
    operation_id="get_simple_moving_average",
    description="Compute the Simple Moving Average (SMA) of close/price values for an entity within a date range. Each output entry is the arithmetic mean of the last `period` values up to and including that date. The first `period - 1` dates have no entry. Works for both OHLC and single-price entities.",
)
async def get_simple_moving_average(
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
    period: Annotated[
        int,
        Query(
            description="Window size in number of data points (e.g. 20, 50, 200). Must be >= 2.",
        ),
    ],
    price_manager: PriceManagerService = Depends(get_price_manager_service),
):
    if period < 2:
        raise HTTPException(status_code=400, detail="period must be >= 2")

    entity = price_manager.db.get_entity_by_id_raw(entity_id)
    if not entity:
        raise ValueError("entity not found")
    timezone = entity.get("timezone")
    if not timezone or not isinstance(timezone, str):
        timezone = "UTC"

    start_dt = convert_to_timezone_aware(start, timezone)
    end_dt = convert_to_timezone_aware(end, timezone)

    series = price_manager.query_close_series(entity_id, start_dt, end_dt)
    data = compute_sma(series, period)
    return {
        "entity_id": str(entity_id),
        "start": start,
        "end": end,
        "period": period,
        "type": "SMA",
        "data": data,
    }


@router.get(
    "/api/v1/prices/ema",
    operation_id="get_exponential_moving_average",
    description="Compute the Exponential Moving Average (EMA) of close/price values for an entity within a date range. Seeded with the SMA of the first `period` data points, then recursed via ema_today = close_today * k + ema_yesterday * (1 - k) where k = 2 / (period + 1). Works for both OHLC and single-price entities.",
)
async def get_exponential_moving_average(
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
    period: Annotated[
        int,
        Query(
            description="Window size in number of data points (e.g. 20, 50, 200). Must be >= 2.",
        ),
    ],
    price_manager: PriceManagerService = Depends(get_price_manager_service),
):
    if period < 2:
        raise HTTPException(status_code=400, detail="period must be >= 2")

    entity = price_manager.db.get_entity_by_id_raw(entity_id)
    if not entity:
        raise ValueError("entity not found")
    timezone = entity.get("timezone")
    if not timezone or not isinstance(timezone, str):
        timezone = "UTC"

    start_dt = convert_to_timezone_aware(start, timezone)
    end_dt = convert_to_timezone_aware(end, timezone)

    series = price_manager.query_close_series(entity_id, start_dt, end_dt)
    data = compute_ema(series, period)
    return {
        "entity_id": str(entity_id),
        "start": start,
        "end": end,
        "period": period,
        "type": "EMA",
        "data": data,
    }


@router.get(
    "/api/v1/prices/rsi",
    operation_id="get_rsi",
    description="Compute the Relative Strength Index (RSI) of close/price values for an entity within a date range. RSI oscillates between 0 and 100; values above 70 are traditionally considered overbought, values below 30 oversold. Uses Wilder's exponential smoothing. Works for both OHLC and single-price entities.",
)
async def get_rsi(
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
    period: Annotated[
        int,
        Query(
            description="RSI lookback period in number of data points (e.g. 14). Must be >= 2.",
        ),
    ],
    price_manager: PriceManagerService = Depends(get_price_manager_service),
):
    if period < 2:
        raise HTTPException(status_code=400, detail="period must be >= 2")

    entity = price_manager.db.get_entity_by_id_raw(entity_id)
    if not entity:
        raise ValueError("entity not found")
    timezone = entity.get("timezone")
    if not timezone or not isinstance(timezone, str):
        timezone = "UTC"

    start_dt = convert_to_timezone_aware(start, timezone)
    end_dt = convert_to_timezone_aware(end, timezone)

    series = price_manager.query_close_series(entity_id, start_dt, end_dt)
    data = compute_rsi(series, period)
    return {
        "entity_id": str(entity_id),
        "start": start,
        "end": end,
        "period": period,
        "type": "RSI",
        "data": data,
    }

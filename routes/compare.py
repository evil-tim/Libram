from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from dependencies import get_price_manager_service
from price_analysis.comparison import build_comparison_payload
from price_management.service import PriceManagerService

router = APIRouter()


@router.get(
    "/api/v1/compare",
    operation_id="compare_entities",
    description="Compare multiple entities side-by-side with summary statistics, optional technical indicators, and relative rankings. Returns a comparison table with per-entity metrics (period return, annualized return, volatility, max drawdown) and cross-entity rankings with delta-from-median. Supports 2-10 entity codes and optional indicator specs like 'sma20', 'rsi14', 'ema:50'.",
)
async def compare_entities(
    entity_codes: Annotated[
        list[str],
        Query(
            description="List of 2-10 entity codes (tickers) to compare. Repeat the parameter for multiple codes: ?entity_codes=MWIDE&entity_codes=EEI"
        ),
    ],
    start: Annotated[
        str,
        Query(
            description="Start date for the comparison range, inclusive. ISO 8601 format: YYYY-MM-DDTHH:MM:SS"
        ),
    ],
    end: Annotated[
        str,
        Query(
            description="End date for the comparison range, exclusive. ISO 8601 format: YYYY-MM-DDTHH:MM:SS"
        ),
    ],
    indicators: Annotated[
        list[str],
        Query(
            description="Optional indicator specs to compute per entity. Presets: 'sma20', 'sma50', 'ema20', 'ema50', 'rsi14'. Custom: 'sma:100', 'ema:12', 'rsi:7'."
        ),
    ] = [],
    normalize_to: Annotated[
        str,
        Query(
            description="Baseline for relative calculations: 'median' (default) or 'first'."
        ),
    ] = "median",
    price_manager: PriceManagerService = Depends(get_price_manager_service),
):
    try:
        return await build_comparison_payload(
            entity_codes=entity_codes,
            start=start,
            end=end,
            indicators=indicators,
            normalize_to=normalize_to,
            price_manager=price_manager,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

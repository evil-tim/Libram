from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from dependencies import get_fundamentals_manager_service
from fundamentals_management import (
    VALID_CONFIDENCE_LEVELS,
    FundamentalsNotFound,
    FundamentalsRequest,
    FundamentalsValidationError,
)
from fundamentals_management.service import FundamentalsManagerService

router = APIRouter()


@router.post(
    "/api/v1/fundamentals",
    operation_id="update_entity_fundamentals",
    description="Upload structured fundamental financial metrics (P/E, market cap, EPS, etc.) for an entity as a timestamped snapshot with provenance and confidence metadata. Metrics are validated against the allowed set; unknown keys are rejected.",
)
async def update_entity_fundamentals(
    body: FundamentalsRequest,
    fundamentals_manager: FundamentalsManagerService = Depends(
        get_fundamentals_manager_service
    ),
):
    try:
        return fundamentals_manager.upload_fundamentals(body)
    except FundamentalsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FundamentalsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/api/v1/fundamentals",
    operation_id="get_entity_fundamentals",
    description="Query stored fundamental financial metrics for an entity.",
)
async def get_entity_fundamentals(
    entity_code: Annotated[
        str, Query(description="Entity code (ticker) to query fundamentals for")
    ],
    mode: Annotated[
        str,
        Query(description="Query mode: 'all' (all snapshots), 'latest_only' (single most recent), 'latest_consolidated' (merged best-per-metric across snapshots)"),
    ] = "latest_only",
    min_confidence: Annotated[
        str,
        Query(
            description="Filter: only use snapshots at this confidence or higher. 'high' > 'medium' > 'low'"
        ),
    ] = "low",
    as_of_date_after: Annotated[
        Optional[str],
        Query(
            description="Filter: only use snapshots with as_of_date >= this ISO date (e.g. 2026-01-01)"
        ),
    ] = None,
    fundamentals_manager: FundamentalsManagerService = Depends(
        get_fundamentals_manager_service
    ),
):
    valid_modes = {"all", "latest_only", "latest_consolidated"}
    if mode not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode: '{mode}'. must be one of: {sorted(valid_modes)}",
        )
    if min_confidence not in VALID_CONFIDENCE_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid min_confidence: '{min_confidence}'. must be one of: {sorted(VALID_CONFIDENCE_LEVELS)}",
        )
    try:
        return fundamentals_manager.fetch_entity_fundamentals(
            entity_code, mode, min_confidence, as_of_date_after
        )
    except FundamentalsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

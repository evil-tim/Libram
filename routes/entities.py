from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from dependencies import get_price_manager_service
from price_management.service import PriceManagerService

router = APIRouter()


@router.get(
    "/api/v1/entities",
    operation_id="list_available_entities",
    description="List available entities currently being tracked. Can be filtered by entity_id, entity_code, or partial match entity_name.",
)
async def list_entities(
    entity_id: Annotated[
        Optional[UUID], Query(description="Filter by entity UUID")
    ] = None,
    entity_code: Annotated[
        Optional[str],
        Query(
            description="Filter by entity code. This can be stock ticker, fund code, etc."
        ),
    ] = None,
    entity_name: Annotated[
        Optional[str], Query(description="Filter by partial entity name")
    ] = None,
    price_manager: PriceManagerService = Depends(get_price_manager_service),
):
    return price_manager.query_entities(entity_id, entity_code, entity_name, None)

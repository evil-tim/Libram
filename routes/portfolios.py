from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from dependencies import get_portfolio_manager_service
from portfolio_management import (
    CreateOrderRequest,
    CreatePortfolioRequest,
    DividendNotFound,
    InsufficientShares,
    OrderNotFound,
    PortfolioDividendNotFound,
    PortfolioDividendUpdateRequest,
    PortfolioNotFound,
    PortfolioValidationError,
    UpdateOrderRequest,
    UpdatePortfolioRequest,
)
from portfolio_management.service import PortfolioManagerService

router = APIRouter()


@router.post(
    "/api/v1/portfolios",
    operation_id="create_portfolio",
    description="Create a named portfolio to group buy/sell orders for tracking investment positions.",
)
async def create_portfolio(
    body: CreatePortfolioRequest,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.create_portfolio(body)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/api/v1/portfolios",
    operation_id="list_portfolios",
    description="List all portfolios ordered by creation time ascending.",
)
async def list_portfolios(
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    return portfolio_manager.list_portfolios()


@router.put(
    "/api/v1/portfolios/{portfolio_id}",
    operation_id="update_portfolio",
    description="Update a portfolio's name.",
)
async def update_portfolio(
    portfolio_id: UUID,
    body: UpdatePortfolioRequest,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.update_portfolio(portfolio_id, body)
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/api/v1/portfolios/{portfolio_id}",
    operation_id="delete_portfolio",
    description="Delete a portfolio and cascade-delete its orders.",
)
async def delete_portfolio(
    portfolio_id: UUID,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        portfolio_manager.delete_portfolio(portfolio_id)
        return {"deleted": True, "id": str(portfolio_id)}
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/api/v1/portfolios/{portfolio_id}/orders",
    operation_id="create_order",
    description="Record a buy or sell order in a portfolio. Resolves entity codes, validates sell sufficiency chronologically, and supports per-order currency via cost_basis_entity_code / fees_entity_code (NULL = PHP).",
)
async def create_order(
    portfolio_id: UUID,
    body: CreateOrderRequest,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.create_order(portfolio_id, body)
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InsufficientShares as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/api/v1/portfolios/{portfolio_id}/orders",
    operation_id="list_orders",
    description="List orders in a portfolio with filtering (entity_code, type, date range), sorting, and pagination.",
)
async def list_orders(
    portfolio_id: UUID,
    page: Annotated[int, Query(description="Zero-indexed page number", ge=0)] = 0,
    size: Annotated[int, Query(description="Page size", ge=1, le=100)] = 20,
    entity_code: Annotated[
        Optional[str], Query(description="Filter by entity ticker")
    ] = None,
    type: Annotated[
        Optional[str], Query(description="Filter by order type: buy or sell")
    ] = None,
    date_from: Annotated[
        Optional[str], Query(description="ISO 8601, orders on or after")
    ] = None,
    date_to: Annotated[
        Optional[str], Query(description="ISO 8601, orders on or before")
    ] = None,
    sort_by: Annotated[
        str, Query(description="Sort field: date, entity_code, shares, cost_basis")
    ] = "date",
    sort_order: Annotated[
        str, Query(description="Sort direction: asc or desc")
    ] = "desc",
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.list_orders(
            portfolio_id,
            page,
            size,
            entity_code,
            type,
            date_from,
            date_to,
            sort_by,
            sort_order,
        )
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    "/api/v1/portfolios/{portfolio_id}/orders/{order_id}",
    operation_id="update_order",
    description="Update an order's fields. Re-validates sell sufficiency if shares, type, date, or entity change.",
)
async def update_order(
    portfolio_id: UUID,
    order_id: UUID,
    body: UpdateOrderRequest,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.update_order(portfolio_id, order_id, body)
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OrderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InsufficientShares as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/api/v1/portfolios/{portfolio_id}/orders/{order_id}",
    operation_id="delete_order",
    description="Delete an order from a portfolio.",
)
async def delete_order(
    portfolio_id: UUID,
    order_id: UUID,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        portfolio_manager.delete_order(order_id)
        return {"deleted": True, "id": str(order_id)}
    except OrderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/v1/portfolios/totals",
    operation_id="get_all_portfolios_totals",
    description="Compute aggregate portfolio totals across ALL portfolios using the average-cost method. Converts non-PHP currencies to PHP via the price table.",
)
async def get_all_portfolios_totals(
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.compute_totals(None)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/api/v1/portfolios/totals/by-entity",
    operation_id="get_all_portfolios_totals_by_entity",
    description="Compute per-entity portfolio totals across ALL portfolios using the average-cost method, with an aggregate totals block.",
)
async def get_all_portfolios_totals_by_entity(
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.compute_totals_by_entity(None)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/api/v1/portfolios/dividends/totals",
    operation_id="get_all_portfolios_dividend_totals",
)
async def get_all_portfolios_dividend_totals(
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.compute_dividend_totals(None)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/api/v1/portfolios/{portfolio_id}/totals",
    operation_id="get_portfolio_totals",
    description="Compute aggregate portfolio totals for a single portfolio using the average-cost method. Converts non-PHP currencies to PHP via the price table.",
)
async def get_portfolio_totals(
    portfolio_id: UUID,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    if not portfolio_manager.db.get_portfolio(portfolio_id):
        raise HTTPException(
            status_code=404, detail=f"portfolio not found: {portfolio_id}"
        )
    try:
        return portfolio_manager.compute_totals(portfolio_id)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/api/v1/portfolios/{portfolio_id}/totals/by-entity",
    operation_id="get_portfolio_totals_by_entity",
    description="Compute per-entity portfolio totals for a single portfolio using the average-cost method, with an aggregate totals block.",
)
async def get_portfolio_totals_by_entity(
    portfolio_id: UUID,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    if not portfolio_manager.db.get_portfolio(portfolio_id):
        raise HTTPException(
            status_code=404, detail=f"portfolio not found: {portfolio_id}"
        )
    try:
        return portfolio_manager.compute_totals_by_entity(portfolio_id)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/api/v1/portfolios/{portfolio_id}/dividends/totals",
    operation_id="get_portfolio_dividend_totals",
)
async def get_portfolio_dividend_totals(
    portfolio_id: UUID,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    if not portfolio_manager.db.get_portfolio(portfolio_id):
        raise HTTPException(
            status_code=404, detail=f"portfolio not found: {portfolio_id}"
        )
    try:
        return portfolio_manager.compute_dividend_totals(portfolio_id)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/api/v1/portfolios/{portfolio_id}/dividends/{dividend_id}",
    operation_id="get_dividend_fee",
)
async def get_dividend_fee(
    portfolio_id: UUID,
    dividend_id: UUID,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.get_dividend_fee(portfolio_id, dividend_id)
    except (PortfolioDividendNotFound, PortfolioNotFound, DividendNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/v1/portfolios/{portfolio_id}/dividends",
    operation_id="list_dividend_fees",
)
async def list_dividend_fees(
    portfolio_id: UUID,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.list_dividend_fees(portfolio_id)
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put(
    "/api/v1/portfolios/{portfolio_id}/dividends/{dividend_id}",
    operation_id="update_dividend_fee",
)
async def update_dividend_fee(
    portfolio_id: UUID,
    dividend_id: UUID,
    body: PortfolioDividendUpdateRequest,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.update_dividend_fee(portfolio_id, dividend_id, body)
    except PortfolioDividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/api/v1/portfolios/{portfolio_id}/dividends/{dividend_id}",
    operation_id="delete_dividend_fee",
)
async def delete_dividend_fee(
    portfolio_id: UUID,
    dividend_id: UUID,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        portfolio_manager.delete_dividend_fee(portfolio_id, dividend_id)
        return {"deleted": True}
    except PortfolioDividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

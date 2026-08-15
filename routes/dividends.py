from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from portfolio_management import (
    DividendEventCreateRequest,
    DividendEventUpdateRequest,
    DividendNotFound,
    PortfolioValidationError,
)
from portfolio_management.service import PortfolioManagerService
from dependencies import get_portfolio_manager_service

router = APIRouter()


@router.post("/api/v1/dividends", operation_id="create_dividend")
async def create_dividend(
    body: DividendEventCreateRequest,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.create_dividend(body)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/dividends", operation_id="list_dividends")
async def list_dividends(
    entity_code: Optional[str] = None,
    ex_date_from: Optional[date] = None,
    ex_date_to: Optional[date] = None,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.list_dividends(entity_code, ex_date_from, ex_date_to)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/dividends/{dividend_id}", operation_id="get_dividend")
async def get_dividend(
    dividend_id: UUID,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.get_dividend(dividend_id)
    except DividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/api/v1/dividends/{dividend_id}", operation_id="update_dividend")
async def update_dividend(
    dividend_id: UUID,
    body: DividendEventUpdateRequest,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        return portfolio_manager.update_dividend(dividend_id, body)
    except DividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/v1/dividends/{dividend_id}", operation_id="delete_dividend")
async def delete_dividend(
    dividend_id: UUID,
    portfolio_manager: PortfolioManagerService = Depends(get_portfolio_manager_service),
):
    try:
        portfolio_manager.delete_dividend(dividend_id)
        return {"deleted": True, "id": str(dividend_id)}
    except DividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

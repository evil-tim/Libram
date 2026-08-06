from decimal import Decimal
from datetime import date
from typing import Optional, Literal
from pydantic import BaseModel, Field

__all__ = [
    "PortfolioNotFound", "PortfolioValidationError", "OrderNotFound", "InsufficientShares",
    "CreatePortfolioRequest", "UpdatePortfolioRequest", "CreateOrderRequest", "UpdateOrderRequest",
    "DividendEventCreateRequest", "DividendEventUpdateRequest",
    "PortfolioDividendCreateRequest", "PortfolioDividendUpdateRequest",
    "DividendNotFound", "PortfolioDividendNotFound",
]

class PortfolioNotFound(Exception): pass
class PortfolioValidationError(Exception): pass
class OrderNotFound(Exception): pass
class InsufficientShares(Exception): pass
class DividendNotFound(Exception): pass
class PortfolioDividendNotFound(Exception): pass

class CreatePortfolioRequest(BaseModel): name: str = Field(..., min_length=1, max_length=255)
class UpdatePortfolioRequest(BaseModel): name: str = Field(..., min_length=1, max_length=255)
class CreateOrderRequest(BaseModel):
    entity_code: str
    date: str
    shares: float = Field(..., gt=0)
    type: Literal["buy", "sell"]
    cost_basis: float = Field(..., ge=0)
    cost_basis_entity_code: Optional[str] = None
    fees: float = Field(0.0, ge=0)
    fees_entity_code: Optional[str] = None
class UpdateOrderRequest(BaseModel):
    entity_code: Optional[str] = None; date: Optional[str] = None
    shares: Optional[float] = Field(None, gt=0); type: Optional[Literal["buy", "sell"]] = None
    cost_basis: Optional[float] = Field(None, ge=0); cost_basis_entity_code: Optional[str] = None
    fees: Optional[float] = Field(None, ge=0); fees_entity_code: Optional[str] = None
class DividendEventCreateRequest(BaseModel):
    entity_code: str
    ex_date: date
    declaration_date: Optional[date] = None
    record_date: Optional[date] = None
    payment_date: Optional[date] = None
    dividend_type: Literal["regular", "special", "return_of_capital"] = "regular"
    amount_per_share: Decimal = Field(..., ge=0)
    amount_per_share_entity_code: Optional[str] = None


class DividendEventUpdateRequest(BaseModel):
    entity_code: Optional[str] = None
    ex_date: Optional[date] = None
    declaration_date: Optional[date] = None
    record_date: Optional[date] = None
    payment_date: Optional[date] = None
    dividend_type: Optional[Literal["regular", "special", "return_of_capital"]] = None
    amount_per_share: Optional[Decimal] = Field(None, ge=0)
    amount_per_share_entity_code: Optional[str] = None


class PortfolioDividendCreateRequest(BaseModel):
    fees: Decimal = Field(Decimal("0"), ge=0)
    fees_entity_code: Optional[str] = None


class PortfolioDividendUpdateRequest(BaseModel):
    fees: Optional[Decimal] = Field(None, ge=0)
    fees_entity_code: Optional[str] = None

from pydantic import BaseModel, Field
from typing import Optional, Literal


class PortfolioNotFound(Exception):
    pass


class PortfolioValidationError(Exception):
    pass


class OrderNotFound(Exception):
    pass


class InsufficientShares(Exception):
    pass


class CreatePortfolioRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class UpdatePortfolioRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class CreateOrderRequest(BaseModel):
    entity_code: str = Field(..., description="Entity ticker (e.g. RCR)")
    date: str = Field(..., description="ISO 8601 datetime")
    shares: float = Field(..., gt=0)
    type: Literal["buy", "sell"]
    cost_basis: float = Field(..., ge=0, description="Per-share price in transaction currency")
    cost_basis_entity_code: Optional[str] = Field(None, description="Currency entity ticker. NULL = PHP")
    fees: float = Field(0.0, ge=0)
    fees_entity_code: Optional[str] = Field(None, description="Currency entity ticker for fees. NULL = PHP")


class UpdateOrderRequest(BaseModel):
    entity_code: Optional[str] = None
    date: Optional[str] = None
    shares: Optional[float] = Field(None, gt=0)
    type: Optional[Literal["buy", "sell"]] = None
    cost_basis: Optional[float] = Field(None, ge=0)
    cost_basis_entity_code: Optional[str] = None
    fees: Optional[float] = Field(None, ge=0)
    fees_entity_code: Optional[str] = None

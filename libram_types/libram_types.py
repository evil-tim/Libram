from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID


@dataclass
class EntityRecord:
    id: UUID
    code: Optional[str] = None
    name: Optional[str] = None
    currency: Optional[str] = None
    datasource: Optional[str] = None
    config: Optional[dict[str, object]] = None
    type: Optional[str] = None
    frequency: Optional[str] = None
    has_weekend: bool = False
    timezone: Optional[str] = None
    min_timestamp: Optional[datetime] = None


@dataclass
class PriceRecord:
    price: Optional[Decimal] = None
    timestamp: Optional[datetime] = None
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None
    timestamp_start: Optional[datetime] = None
    timestamp_end: Optional[datetime] = None


@dataclass
class TaskRecord:
    id: UUID
    entity_id: UUID
    timestamp_start: Optional[datetime] = None
    timestamp_end: Optional[datetime] = None
    status: Optional[str] = None
    retry_count: Optional[int] = None
    created_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None


@dataclass
class SnapshotStateRecord:
    entity_id: UUID
    enabled: bool
    interval_seconds: int
    next_due_at: datetime
    lease_token: Optional[UUID] = None
    lease_expires_at: Optional[datetime] = None
    worker_id: Optional[str] = None
    attempt_count: int = 0
    consecutive_failures: int = 0
    last_started_at: Optional[datetime] = None
    last_succeeded_at: Optional[datetime] = None
    last_failed_at: Optional[datetime] = None
    last_observed_at: Optional[datetime] = None
    last_duration_ms: Optional[int] = None
    last_error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class PortfolioRecord:
    id: UUID
    name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    description: Optional[str] = None


@dataclass
class PortfolioOrderRecord:
    id: UUID
    portfolio_id: UUID
    entity_id: UUID
    date: Optional[datetime] = None
    shares: Optional[Decimal] = None
    type: Optional[str] = None
    cost_basis: Optional[Decimal] = None
    cost_basis_entity_id: Optional[UUID] = None
    fees: Optional[Decimal] = None
    fees_entity_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class DividendEventRecord:
    id: UUID
    entity_id: UUID
    ex_date: date
    declaration_date: Optional[date] = None
    record_date: Optional[date] = None
    payment_date: Optional[date] = None
    dividend_type: Optional[str] = None
    amount_per_share: Optional[Decimal] = None
    amount_per_share_entity_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class PortfolioDividendRecord:
    id: UUID
    portfolio_id: UUID
    dividend_event_id: UUID
    fees: Optional[Decimal] = None
    fees_entity_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class DailyPrice:
    """One observation per UTC calendar day: that day's last recorded value."""

    day: date
    observed_at: datetime
    value: Decimal


@dataclass(frozen=True)
class FxPath:
    """A resolved single-hop conversion between two currencies.

    ``rate_entity_id`` names the currency entity whose own price series supplies
    the rate, and ``direction`` is ``"direct"`` (multiply) or ``"inverse"``
    (divide).
    """

    rate_entity_id: UUID
    direction: str


# ---------------------------------------------------------------------------
# entity groups (fused entity data)
# ---------------------------------------------------------------------------


@dataclass
class EntityGroupRecord:
    """A named fusion context: one top-level entity assembled from several sources.

    ``quote_currency_id`` is the output currency for fused values; ``None`` means
    PHP, matching ``entity.currency_id``.
    """

    id: UUID
    code: str
    name: str
    description: Optional[str] = None
    quote_currency_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class EntityGroupMemberRecord:
    """Explicit membership of one source entity in one group.

    ``strength`` is ``strong`` (the actual reference) or ``weak`` (a derivative or
    wrapped representation of it).
    """

    group_id: UUID
    entity_id: UUID
    strength: str
    created_at: Optional[datetime] = None


@dataclass
class EntityGroupMemberDetail:
    """A member joined to the entity it names, for responses.

    ``code`` is unique only per datasource, so a member reference always pairs
    ``entity_code`` with ``datasource``.
    """

    entity_id: UUID
    entity_code: str
    datasource: str
    strength: str
    created_at: Optional[datetime] = None

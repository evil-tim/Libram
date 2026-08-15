import os
from datetime import date
from typing import Annotated, Optional
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from libram_database.db import Database
from price_management.service import PriceManagerService
from price_scheduler.service import PriceSchedulerService
from price_analysis import compute_sma, compute_ema, compute_rsi, convert_to_timezone_aware
from price_analysis.comparison import build_comparison_payload
from fundamentals_management import FundamentalsRequest, FundamentalsNotFound, FundamentalsValidationError, VALID_CONFIDENCE_LEVELS
from fundamentals_management.client import FundamentalsManagerClient
from portfolio_management import (
    CreateOrderRequest,
    CreatePortfolioRequest,
    InsufficientShares,
    OrderNotFound,
    PortfolioNotFound,
    PortfolioValidationError,
    UpdateOrderRequest,
    UpdatePortfolioRequest,
    DividendEventCreateRequest, DividendEventUpdateRequest,
    PortfolioDividendCreateRequest, PortfolioDividendUpdateRequest,
    DividendNotFound, PortfolioDividendNotFound,
)
from portfolio_management.client import PortfolioManagerClient

from cli_schedule import build_all_tasks

""" Dependencies """

async def get_db_string() -> str:
    load_dotenv()
    db_string = os.getenv("LIBRAM_DB")
    if not db_string:
        raise RuntimeError("LIBRAM_DB environment variable not set")
    return db_string


async def get_database(db_string: str = Depends(get_db_string)) -> Database:
    return Database(db_string)


async def get_price_manager_client(
    db: Database = Depends(get_database),
) -> PriceManagerService:
    return PriceManagerService(db)

async def get_fundamentals_manager_client(
    price_manager: PriceManagerService = Depends(get_price_manager_client),
    db: Database = Depends(get_database),
) -> FundamentalsManagerClient:
    return FundamentalsManagerClient(price_manager, db)

async def get_portfolio_manager_client(
    price_manager: PriceManagerService = Depends(get_price_manager_client),
    db: Database = Depends(get_database),
) -> PortfolioManagerClient:
    return PortfolioManagerClient(price_manager, db)

async def get_scheduler_client(
    price_manager: PriceManagerService = Depends(get_price_manager_client),
    db: Database = Depends(get_database),
) -> PriceSchedulerService:
    return PriceSchedulerService(price_manager, db)

""" FastAPI lifecycle """

def startup(_app: FastAPI):
    # Run idempotent schema DDL to create any missing tables/indexes
    load_dotenv()
    # TODO: rethink this, does not work since the db user has restricted permissions
    # db_string = os.getenv("LIBRAM_DB")
    # if db_string:
    #    db = Database(db_string)
    #    db.init_db()
    #    print("database schema initialised from schema.sql")
    print("Starting up server...")


def shutdown(_app: FastAPI):
    # noop
    print("Shutting down server...")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup(_app=_app)
    yield
    shutdown(_app=_app)

""" FastAPI app and routes """

app = FastAPI(lifespan=lifespan, name="Libram Price Feed API", version="1.0.0")


# entity endpoints
@app.get(
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
    price_manager: PriceManagerService = Depends(get_price_manager_client),
):
    return price_manager.query_entities(entity_id, entity_code, entity_name, None)


# price endpoints
@app.get(
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
    price_manager: PriceManagerService = Depends(get_price_manager_client),
):
    entity = price_manager.db.get_entity_by_id_raw(entity_id)
    if not entity:
        raise ValueError("entity not found")
    timezone = entity.get("timezone")
    if not timezone or not isinstance(timezone, str):
        timezone = "UTC"  # default to UTC if timezone not specified

    # breakdown the start date string into components to construct a timezone-aware datetime in the entity's timezone
    start_dt = convert_to_timezone_aware(start, timezone)
    end_dt = convert_to_timezone_aware(end, timezone)

    return price_manager.query_prices(entity_id, start_dt, end_dt, page, size)


@app.get(
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
    price_manager: PriceManagerService = Depends(get_price_manager_client),
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


@app.get(
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
    price_manager: PriceManagerService = Depends(get_price_manager_client),
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


@app.get(
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
    price_manager: PriceManagerService = Depends(get_price_manager_client),
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


@app.get(
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
    price_manager: PriceManagerService = Depends(get_price_manager_client),
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


@app.get(
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
        Query(description="Baseline for relative calculations: 'median' (default) or 'first'."),
    ] = "median",
    price_manager: PriceManagerService = Depends(get_price_manager_client),
):
    """Compare multiple entities with relative rankings."""
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


@app.post(
    "/api/v1/fundamentals",
    operation_id="update_entity_fundamentals",
    description="Upload structured fundamental financial metrics (P/E, market cap, EPS, etc.) for an entity as a timestamped snapshot with provenance and confidence metadata. Metrics are validated against the allowed set; unknown keys are rejected.",
)
async def update_entity_fundamentals(
    body: FundamentalsRequest,
    fundamentals_manager: FundamentalsManagerClient = Depends(get_fundamentals_manager_client),
):
    try:
        return fundamentals_manager.upload_fundamentals(body)
    except FundamentalsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FundamentalsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/v1/fundamentals",
    operation_id="get_entity_fundamentals",
    description="Query stored fundamental financial metrics for an entity. Returns one or more timestamped snapshots ordered by recency. mode='latest_only' (default) returns the most recent snapshot; mode='all' returns every snapshot; mode='latest_consolidated' merges the best available value for each metric across snapshots. Optional min_confidence and as_of_date_after filters apply to all modes.",
)
async def get_entity_fundamentals(
    entity_code: Annotated[str, Query(description="Entity code (ticker) to query fundamentals for")],
    mode: Annotated[str, Query(description="Query mode: 'all' (all snapshots), 'latest_only' (single most recent), 'latest_consolidated' (merged best-per-metric across snapshots)")] = "latest_only",
    min_confidence: Annotated[str, Query(description="Filter: only use snapshots at this confidence or higher. 'high' > 'medium' > 'low'")] = "low",
    as_of_date_after: Annotated[Optional[str], Query(description="Filter: only use snapshots with as_of_date >= this ISO date (e.g. 2026-01-01)")] = None,
    fundamentals_manager: FundamentalsManagerClient = Depends(get_fundamentals_manager_client),
):
    valid_modes = {"all", "latest_only", "latest_consolidated"}
    if mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"invalid mode: '{mode}'. must be one of: {sorted(valid_modes)}")
    if min_confidence not in VALID_CONFIDENCE_LEVELS:
        raise HTTPException(status_code=400, detail=f"invalid min_confidence: '{min_confidence}'. must be one of: {sorted(VALID_CONFIDENCE_LEVELS)}")
    try:
        return fundamentals_manager.fetch_entity_fundamentals(entity_code, mode, min_confidence, as_of_date_after)
    except FundamentalsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# portfolio endpoints
@app.post(
    "/api/v1/portfolios",
    operation_id="create_portfolio",
    description="Create a named portfolio to group buy/sell orders for tracking investment positions.",
)
async def create_portfolio(
    body: CreatePortfolioRequest,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.create_portfolio(body)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/v1/portfolios",
    operation_id="list_portfolios",
    description="List all portfolios ordered by creation time ascending.",
)
async def list_portfolios(
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    return portfolio_manager.list_portfolios()


@app.put(
    "/api/v1/portfolios/{portfolio_id}",
    operation_id="update_portfolio",
    description="Update a portfolio's name.",
)
async def update_portfolio(
    portfolio_id: UUID,
    body: UpdatePortfolioRequest,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.update_portfolio(portfolio_id, body)
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete(
    "/api/v1/portfolios/{portfolio_id}",
    operation_id="delete_portfolio",
    description="Delete a portfolio and cascade-delete its orders.",
)
async def delete_portfolio(
    portfolio_id: UUID,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        portfolio_manager.delete_portfolio(portfolio_id)
        return {"deleted": True, "id": str(portfolio_id)}
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/v1/portfolios/{portfolio_id}/orders",
    operation_id="create_order",
    description="Record a buy or sell order in a portfolio. Resolves entity codes, validates sell sufficiency chronologically, and supports per-order currency via cost_basis_entity_code / fees_entity_code (NULL = PHP).",
)
async def create_order(
    portfolio_id: UUID,
    body: CreateOrderRequest,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.create_order(portfolio_id, body)
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InsufficientShares as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/v1/portfolios/{portfolio_id}/orders",
    operation_id="list_orders",
    description="List orders in a portfolio with filtering (entity_code, type, date range), sorting, and pagination.",
)
async def list_orders(
    portfolio_id: UUID,
    page: Annotated[int, Query(description="Zero-indexed page number", ge=0)] = 0,
    size: Annotated[int, Query(description="Page size", ge=1, le=100)] = 20,
    entity_code: Annotated[Optional[str], Query(description="Filter by entity ticker")] = None,
    type: Annotated[Optional[str], Query(description="Filter by order type: buy or sell")] = None,
    date_from: Annotated[Optional[str], Query(description="ISO 8601, orders on or after")] = None,
    date_to: Annotated[Optional[str], Query(description="ISO 8601, orders on or before")] = None,
    sort_by: Annotated[str, Query(description="Sort field: date, entity_code, shares, cost_basis")] = "date",
    sort_order: Annotated[str, Query(description="Sort direction: asc or desc")] = "desc",
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.list_orders(
            portfolio_id, page, size, entity_code, type, date_from, date_to, sort_by, sort_order
        )
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put(
    "/api/v1/portfolios/{portfolio_id}/orders/{order_id}",
    operation_id="update_order",
    description="Update an order's fields. Re-validates sell sufficiency if shares, type, date, or entity change.",
)
async def update_order(
    portfolio_id: UUID,
    order_id: UUID,
    body: UpdateOrderRequest,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
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


@app.delete(
    "/api/v1/portfolios/{portfolio_id}/orders/{order_id}",
    operation_id="delete_order",
    description="Delete an order from a portfolio.",
)
async def delete_order(
    portfolio_id: UUID,
    order_id: UUID,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        portfolio_manager.delete_order(order_id)
        return {"deleted": True, "id": str(order_id)}
    except OrderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/v1/portfolios/totals",
    operation_id="get_all_portfolios_totals",
    description="Compute aggregate portfolio totals across ALL portfolios using the average-cost method. Converts non-PHP currencies to PHP via the price table.",
)
async def get_all_portfolios_totals(
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.compute_totals(None)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/v1/portfolios/totals/by-entity",
    operation_id="get_all_portfolios_totals_by_entity",
    description="Compute per-entity portfolio totals across ALL portfolios using the average-cost method, with an aggregate totals block.",
)
async def get_all_portfolios_totals_by_entity(
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.compute_totals_by_entity(None)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/portfolios/dividends/totals", operation_id="get_all_portfolios_dividend_totals")
async def get_all_portfolios_dividend_totals(
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.compute_dividend_totals(None)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/v1/portfolios/{portfolio_id}/totals",
    operation_id="get_portfolio_totals",
    description="Compute aggregate portfolio totals for a single portfolio using the average-cost method. Converts non-PHP currencies to PHP via the price table.",
)
async def get_portfolio_totals(
    portfolio_id: UUID,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    if not portfolio_manager.db.get_portfolio(portfolio_id):
        raise HTTPException(status_code=404, detail=f"portfolio not found: {portfolio_id}")
    try:
        return portfolio_manager.compute_totals(portfolio_id)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/v1/portfolios/{portfolio_id}/totals/by-entity",
    operation_id="get_portfolio_totals_by_entity",
    description="Compute per-entity portfolio totals for a single portfolio using the average-cost method, with an aggregate totals block.",
)
async def get_portfolio_totals_by_entity(
    portfolio_id: UUID,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    if not portfolio_manager.db.get_portfolio(portfolio_id):
        raise HTTPException(status_code=404, detail=f"portfolio not found: {portfolio_id}")
    try:
        return portfolio_manager.compute_totals_by_entity(portfolio_id)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/portfolios/{portfolio_id}/dividends/totals", operation_id="get_portfolio_dividend_totals")
async def get_portfolio_dividend_totals(
    portfolio_id: UUID,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    if not portfolio_manager.db.get_portfolio(portfolio_id):
        raise HTTPException(status_code=404, detail=f"portfolio not found: {portfolio_id}")
    try:
        return portfolio_manager.compute_dividend_totals(portfolio_id)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/dividends", operation_id="create_dividend")
async def create_dividend(
    body: DividendEventCreateRequest,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.create_dividend(body)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/dividends", operation_id="list_dividends")
async def list_dividends(
    entity_code: Optional[str] = None,
    ex_date_from: Optional[date] = None,
    ex_date_to: Optional[date] = None,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.list_dividends(entity_code, ex_date_from, ex_date_to)
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/dividends/{dividend_id}", operation_id="get_dividend")
async def get_dividend(
    dividend_id: UUID,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.get_dividend(dividend_id)
    except DividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/v1/dividends/{dividend_id}", operation_id="update_dividend")
async def update_dividend(
    dividend_id: UUID,
    body: DividendEventUpdateRequest,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.update_dividend(dividend_id, body)
    except DividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/v1/dividends/{dividend_id}", operation_id="delete_dividend")
async def delete_dividend(
    dividend_id: UUID,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        portfolio_manager.delete_dividend(dividend_id)
        return {"deleted": True, "id": str(dividend_id)}
    except DividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/v1/portfolios/{portfolio_id}/dividends/{dividend_id}",
    operation_id="create_dividend_fee",
)
async def create_dividend_fee(
    portfolio_id: UUID,
    dividend_id: UUID,
    body: PortfolioDividendCreateRequest,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.create_dividend_fee(portfolio_id, dividend_id, body)
    except PortfolioDividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PortfolioNotFound, DividendNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/v1/portfolios/{portfolio_id}/dividends/{dividend_id}",
    operation_id="get_dividend_fee",
)
async def get_dividend_fee(
    portfolio_id: UUID,
    dividend_id: UUID,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.get_dividend_fee(portfolio_id, dividend_id)
    except (PortfolioDividendNotFound, PortfolioNotFound, DividendNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/v1/portfolios/{portfolio_id}/dividends", operation_id="list_dividend_fees"
)
async def list_dividend_fees(
    portfolio_id: UUID,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.list_dividend_fees(portfolio_id)
    except PortfolioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put(
    "/api/v1/portfolios/{portfolio_id}/dividends/{dividend_id}",
    operation_id="update_dividend_fee",
)
async def update_dividend_fee(
    portfolio_id: UUID,
    dividend_id: UUID,
    body: PortfolioDividendUpdateRequest,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        return portfolio_manager.update_dividend_fee(portfolio_id, dividend_id, body)
    except PortfolioDividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PortfolioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete(
    "/api/v1/portfolios/{portfolio_id}/dividends/{dividend_id}",
    operation_id="delete_dividend_fee",
)
async def delete_dividend_fee(
    portfolio_id: UUID,
    dividend_id: UUID,
    portfolio_manager: PortfolioManagerClient = Depends(get_portfolio_manager_client),
):
    try:
        portfolio_manager.delete_dividend_fee(portfolio_id, dividend_id)
        return {"deleted": True}
    except PortfolioDividendNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


""" MCP setup to expose PriceSchedulerClient methods as MCP endpoints under /mcp path with stateless HTTP transport """

mcp = FastMCP.from_fastapi(app=app, name="Libram Price Feed MCP", version="1.0.0")
mcp_app = mcp.http_app(path="/mcp", stateless_http=True, transport="http")

combined_app = FastAPI(
    name="Libram Price Feed API with MCP",
    routes=[
        *mcp_app.routes,  # MCP routes
        *app.routes,  # Original API routes
    ],
    lifespan=combine_lifespans(lifespan, mcp_app.lifespan),
)

combined_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

""" Scheduler setup to run build_all_tasks every day at 8:00 and 20:00 """


def build_all_tasks_no_args():
    build_all_tasks(None)


scheduler = BackgroundScheduler()
# Schedule the build_all_tasks function to run at 8:00 and 20:00 every day
scheduler.add_job(build_all_tasks_no_args, CronTrigger(hour="8,20", minute="0"))
scheduler.start()

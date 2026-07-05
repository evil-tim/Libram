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
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from libram_database.db import Database
from price_management.client import PriceManagerClient
from price_scheduler.client import PriceSchedulerClient
from price_analysis import compute_sma, compute_ema, compute_rsi, convert_to_timezone_aware
from price_analysis.comparison import build_comparison_payload

from cli_schedule import build_all_tasks

""" Constants """

ALLOWED_FUNDAMENTAL_METRICS = {
    "market_cap",        # PHP millions
    "pe_ratio",          # x (trailing unless noted)
    "pb_ratio",          # x
    "eps",               # PHP
    "shares_outstanding", # millions
    "dividend_yield",    # percent (e.g., 3.55 for 3.55%)
    "net_income_ttm",    # PHP millions
}

VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}

""" Request / Response Models """

class FundamentalsRequest(BaseModel):
    entity_code: str
    metrics: dict[str, float]
    source_name: str
    source_url: str = ""
    as_of_date: str = ""
    confidence: str = "medium"
    notes: str = ""

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
) -> PriceManagerClient:
    return PriceManagerClient(db)


async def get_scheduler_client(
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
    db: Database = Depends(get_database),
) -> PriceSchedulerClient:
    return PriceSchedulerClient(price_manager, db)

""" FastAPI lifecycle """

def startup(_app: FastAPI):
    # Run idempotent schema DDL to create any missing tables/indexes
    load_dotenv()
    db_string = os.getenv("LIBRAM_DB")
    if db_string:
        db = Database(db_string)
        db.init_db()
        print("database schema initialised from schema.sql")
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
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
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
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
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
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
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
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
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
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
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
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
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
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
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


def _format_fundamentals_response(row: dict, entity: Optional[dict]) -> dict:
    """Format a raw entity_fundamentals DB row into the API response shape."""
    metrics_raw = row.get("metrics", {})
    # Ensure all seven metric keys are present (null for missing)
    metrics = {k: metrics_raw.get(k) for k in ALLOWED_FUNDAMENTAL_METRICS}

    uploaded_at = row.get("uploaded_at")
    as_of_date = row.get("as_of_date")

    return {
        "snapshot_id": row.get("id"),
        "entity_id": str(row.get("entity_id")),
        "entity_code": entity.get("code") if entity else None,
        "entity_name": entity.get("name") if entity else None,
        "metrics": metrics,
        "source": {
            "name": row.get("source_name"),
            "url": row.get("source_url", ""),
            "as_of_date": str(as_of_date) if as_of_date else None,
            "confidence": row.get("confidence"),
            "notes": row.get("notes", ""),
        },
        "uploaded_at": uploaded_at.isoformat() if uploaded_at is not None else None,
        "uploaded_by": row.get("uploaded_by", "agent"),
    }


@app.post(
    "/api/v1/fundamentals",
    operation_id="update_entity_fundamentals",
    description="Upload structured fundamental financial metrics (P/E, market cap, EPS, etc.) for an entity as a timestamped snapshot with provenance and confidence metadata. Metrics are validated against the allowed set; unknown keys are rejected.",
)
async def update_entity_fundamentals(
    body: FundamentalsRequest,
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
):
    # Validate entity exists
    entities = price_manager.query_entities(None, body.entity_code, None, None)
    entities_list = list(entities)
    if not entities_list:
        raise HTTPException(status_code=404, detail=f"entity not found: {body.entity_code}")
    entity = entities_list[0]
    entity_id = entity.id

    # Validate metrics keys
    unknown_keys = set(body.metrics.keys()) - ALLOWED_FUNDAMENTAL_METRICS
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail=f"unknown metric keys: {sorted(unknown_keys)}. allowed: {sorted(ALLOWED_FUNDAMENTAL_METRICS)}",
        )

    # Validate metric values are numeric (Pydantic already enforces float, but null check)
    for key, value in body.metrics.items():
        if value is None:
            raise HTTPException(status_code=400, detail=f"metric '{key}' has null value; omit the key instead")

    # Validate confidence
    if body.confidence not in VALID_CONFIDENCE_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid confidence: '{body.confidence}'. must be one of: {sorted(VALID_CONFIDENCE_LEVELS)}",
        )

    # Default as_of_date to today
    as_of_date = body.as_of_date if body.as_of_date else str(date.today())

    # Upload fundamentals
    row = price_manager.upload_fundamentals(
        entity_id=entity_id,
        metrics=body.metrics,
        source_name=body.source_name,
        source_url=body.source_url,
        as_of_date=as_of_date,
        confidence=body.confidence,
        notes=body.notes,
    )

    # Resolve entity raw for response
    entity_raw = price_manager.db.get_entity_by_id_raw(entity_id)
    return _format_fundamentals_response(row, entity_raw)


@app.get(
    "/api/v1/fundamentals",
    operation_id="get_entity_fundamentals",
    description="Query stored fundamental financial metrics for an entity. Returns one or more timestamped snapshots ordered by recency. Use latest_only=true (default) for the most recent snapshot only.",
)
async def get_entity_fundamentals(
    entity_code: Annotated[str, Query(description="Entity code (ticker) to query fundamentals for")],
    latest_only: Annotated[bool, Query(description="If true, return only the most recent snapshot")] = True,
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
):
    entity_raw, rows = price_manager.get_fundamentals(entity_code, latest_only=latest_only)
    if entity_raw is None:
        raise HTTPException(status_code=404, detail=f"entity not found: {entity_code}")

    return [_format_fundamentals_response(row, entity_raw) for row in rows]


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

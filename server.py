import asyncio
import os
import re
import statistics
from datetime import datetime
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
from price_management.client import PriceManagerClient
from price_scheduler.client import PriceSchedulerClient
from price_analysis import compute_sma, compute_ema, compute_rsi, compute_max_drawdown, convert_to_timezone_aware

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
) -> PriceManagerClient:
    return PriceManagerClient(db)


async def get_scheduler_client(
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
    db: Database = Depends(get_database),
) -> PriceSchedulerClient:
    return PriceSchedulerClient(price_manager, db)

""" FastAPI lifecycle """

def startup(_app: FastAPI):
    # noop
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


# --- indicator parsing helpers ---

# Preset: "sma20" -> ("sma", 20); Custom: "sma:100" -> ("sma", 100)
_INDICATOR_RE = re.compile(r"^(sma|ema|rsi)(?::?(\d+))$")

def _parse_indicator(spec: str) -> Optional[tuple[str, int]]:
    """Parse an indicator spec string into (type, period) or None if invalid."""
    m = _INDICATOR_RE.match(spec.strip().lower())
    if not m:
        return None
    kind = m.group(1)
    period = int(m.group(2)) if m.group(2) else {"sma": 20, "ema": 20, "rsi": 14}[kind]
    if period < 2:
        return None
    return (kind, period)


def _compute_indicator(kind: str, period: int, series: list[tuple[datetime, float]]) -> Optional[dict]:
    """Compute an indicator and return the latest data point, or None if not enough data."""
    if kind == "sma":
        data = compute_sma(series, period)
    elif kind == "ema":
        data = compute_ema(series, period)
    elif kind == "rsi":
        data = compute_rsi(series, period)
    else:
        return None
    if not data:
        return None
    last = data[-1]
    return {"latest": last["value"], "latest_date": last["date"]}


async def _resolve_and_fetch_entity(
    code: str,
    start_dt: datetime,
    end_dt: datetime,
    price_manager: PriceManagerClient,
    indicator_specs: list[tuple[str, int]],
) -> dict:
    """Resolve an entity code and fetch its summary + indicators.

    Returns a dict matching the entities[] response schema entry.
    """
    # Resolve entity
    entities = price_manager.query_entities(None, code, None, None)
    entity_list = list(entities) if entities else []
    if not entity_list:
        return {
            "code": code,
            "name": None,
            "status": "not_found",
            "summary": None,
            "indicators": None,
        }

    entity = entity_list[0]
    entity_id = entity.id
    entity_name = entity.name
    timezone = getattr(entity, "timezone", None) or "UTC"

    # Convert dates to entity's timezone
    tz_start = convert_to_timezone_aware(start_dt.strftime("%Y-%m-%dT%H:%M:%S"), timezone)
    tz_end = convert_to_timezone_aware(end_dt.strftime("%Y-%m-%dT%H:%M:%S"), timezone)

    # Fetch price summary
    summary_raw = price_manager.query_price_summary(entity_id, tz_start, tz_end)
    if not summary_raw:
        return {
            "code": code,
            "name": entity_name,
            "status": "no_data",
            "summary": None,
            "indicators": None,
        }

    # Fetch close series for max drawdown + indicators
    series = price_manager.query_close_series(entity_id, tz_start, tz_end)

    # Compute derived metrics (dict values are typed as 'object' by the DB layer;
    # we know they are numeric at runtime from the SQL aggregation)
    count_val: int = summary_raw.get("count", 0)  # type: ignore[assignment]
    period_return_val: float = summary_raw.get("period_return_pct", 0.0)  # type: ignore[assignment]
    avg_val: float = summary_raw.get("avg", 0.0)  # type: ignore[assignment]
    std_dev_val: float = summary_raw.get("std_dev", 0.0)  # type: ignore[assignment]

    count = int(count_val) if count_val is not None else 0
    period_return_pct = float(period_return_val) if period_return_val is not None else 0.0
    annualized_return_pct = round(period_return_pct * (252 / count), 2) if count > 0 else 0.0

    avg = float(avg_val) if avg_val is not None else 0.0
    std_dev = float(std_dev_val) if std_dev_val is not None else 0.0
    volatility_pct = round((std_dev / avg) * 100, 2) if avg and avg != 0 else 0.0

    max_drawdown_pct = compute_max_drawdown(series) if series else 0.0

    summary = {
        "count": count,
        "first_close": summary_raw.get("first_close"),
        "last_close": summary_raw.get("last_close"),
        "min": summary_raw.get("min"),
        "max": summary_raw.get("max"),
        "avg": summary_raw.get("avg"),
        "std_dev": summary_raw.get("std_dev"),
        "period_return_pct": period_return_pct,
        "annualized_return_pct": annualized_return_pct,
        "volatility_pct": volatility_pct,
        "max_drawdown_pct": max_drawdown_pct,
    }

    # Compute indicators
    indicators = {}
    for kind, period in indicator_specs:
        key = f"{kind}{period}"
        try:
            result = _compute_indicator(kind, period, series)
            indicators[key] = result
        except Exception:
            indicators[key] = None

    return {
        "code": code,
        "name": entity_name,
        "status": "ok",
        "summary": summary,
        "indicators": indicators if indicators else None,
    }


def _standard_competition_rank(values: list[tuple[str, float]], metric: str, reverse: bool = True) -> list[dict]:
    """Rank entities by a metric using standard competition ranking.

    reverse=True: higher is better (returns). reverse=False: lower is better (volatility, drawdown).
    Returns list of {code, value, rank, delta_from_baseline}.
    """
    # Sort by value
    sorted_vals = sorted(values, key=lambda x: x[1], reverse=reverse)

    ranked = []
    current_rank = 1
    for i, (code, val) in enumerate(sorted_vals):
        if i > 0 and val != sorted_vals[i - 1][1]:
            current_rank = i + 1
        ranked.append({"code": code, "value": val, "rank": current_rank})

    # Compute baseline (median of ok entities' values)
    ok_values = [v for _, v in values]
    if ok_values:
        baseline = statistics.median(ok_values)
    else:
        baseline = 0.0

    for entry in ranked:
        entry["delta_from_baseline"] = round(entry["value"] - baseline, 2)

    return ranked


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
    # --- Validation ---
    if len(entity_codes) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 entities to compare.")
    if len(entity_codes) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 entities per comparison.")

    # Parse start/end (we'll convert per-entity timezone later, but validate format now)
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S")
        end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DDTHH:MM:SS.")

    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end.")

    # Parse indicators
    parsed_indicators: list[tuple[str, int]] = []
    unknown_indicators: list[str] = []
    for spec in indicators:
        parsed = _parse_indicator(spec)
        if parsed:
            parsed_indicators.append(parsed)
        else:
            unknown_indicators.append(spec)

    # --- Fetch all entities in parallel ---
    tasks = [
        _resolve_and_fetch_entity(code, start_dt, end_dt, price_manager, parsed_indicators)
        for code in entity_codes
    ]
    entity_results = await asyncio.gather(*tasks)

    # --- Build rankings for ok entities ---
    ok_entities = [e for e in entity_results if e["status"] == "ok"]

    rankings: dict[str, list[dict]] = {}

    if ok_entities:
        # Rank each summary metric
        for metric, reverse in [
            ("period_return_pct", True),
            ("annualized_return_pct", True),
            ("volatility_pct", False),
            ("max_drawdown_pct", True),  # less negative = better
        ]:
            values = [(e["code"], e["summary"][metric]) for e in ok_entities]
            rankings[metric] = _standard_competition_rank(values, metric, reverse=reverse)

        # Rank each indicator metric (latest value)
        for kind, period in parsed_indicators:
            key = f"{kind}{period}"
            values = []
            for e in ok_entities:
                ind = e.get("indicators", {})
                if ind and key in ind and ind[key] is not None:
                    values.append((e["code"], ind[key]["latest"]))
            if values:
                # Higher is better for SMA/EMA (trend); RSI: neutral (neither higher nor lower is inherently better)
                # We'll rank RSI as "neutral" — ascending order (lower RSI = rank 1)
                reverse = kind != "rsi"
                rankings[key] = _standard_competition_rank(values, key, reverse=reverse)

    # --- Compute baseline ---
    baseline: dict[str, object] = {"metric": normalize_to}
    if ok_entities:
        for metric in ["period_return_pct", "annualized_return_pct", "volatility_pct", "max_drawdown_pct"]:
            vals = [e["summary"][metric] for e in ok_entities]
            baseline[metric] = round(statistics.median(vals), 2)
        for kind, period in parsed_indicators:
            key = f"{kind}{period}"
            vals = []
            for e in ok_entities:
                ind = e.get("indicators", {})
                if ind and key in ind and ind[key] is not None:
                    vals.append(ind[key]["latest"])
            if vals:
                baseline[key] = round(statistics.median(vals), 4)
    else:
        baseline["note"] = "No valid entities to compute baseline."

    # --- Build response ---
    from datetime import timezone as tz

    meta = {
        "start": start,
        "end": end,
        "entity_count": len(entity_codes),
        "requested_indicators": indicators,
        "normalize_to": normalize_to,
        "generated_at": datetime.now(tz.utc).isoformat(),
    }
    if unknown_indicators:
        meta["warnings"] = [f"Unknown indicator spec: {s}" for s in unknown_indicators]

    return {
        "meta": meta,
        "entities": entity_results,
        "rankings": rankings,
        "baseline": baseline,
    }


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

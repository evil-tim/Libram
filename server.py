from datetime import datetime
from zoneinfo import ZoneInfo
import os
from typing import Annotated, Optional
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from libram_database.db import Database
from price_management.client import PriceManagerClient
from price_scheduler.client import PriceSchedulerClient

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


def convert_to_timezone_aware(date_str: str, timezone_str: str) -> datetime:
    # parse the date string into components
    dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
    year = dt.year
    month = dt.month
    day = dt.day
    hour = dt.hour
    minute = dt.minute
    second = dt.second
    return datetime(
        year, month, day, hour, minute, second, tzinfo=ZoneInfo(timezone_str)
    )


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
scheduler.add_job(build_all_tasks, CronTrigger(hour="8,20", minute="0"))
scheduler.start()

from datetime import datetime
import os
from typing import Optional
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.concurrency import asynccontextmanager

from libram_database.db import Database
from price_management.client import PriceManagerClient
from price_scheduler.client import PriceSchedulerClient


def startup(_app: FastAPI):
    print("Starting up server...")

    # Load environment variables from .env file
    load_dotenv()
    db_string = os.getenv("LIBRAM_DB")
    if not db_string:
        raise RuntimeError("LIBRAM_DB environment variable not set")

    # Initialize objects
    db = Database(db_string)
    price_manager = PriceManagerClient(db)
    scheduler = PriceSchedulerClient(price_manager, db)

    # Store objects in app state for access in route handlers
    _app.state.db = db
    _app.state.price_manager = price_manager
    _app.state.scheduler = scheduler


def shutdown(_app: FastAPI):
    # noop
    print("Shutting down server...")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup(_app=_app)
    yield
    shutdown(_app=_app)

app = FastAPI(lifespan=lifespan)

# entity endpoints
@app.get("/api/v1/entities")
async def list_entities(entity_id: Optional[UUID] = None, entity_code: Optional[str] = None, entity_name: Optional[str] = None):
    price_manager = getattr(app.state, "price_manager", None)
    if not price_manager and not isinstance(price_manager, PriceManagerClient):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Price manager not initialized")

    return price_manager.query_entities(entity_id, entity_code, entity_name, None)

# price endpoints
@app.get("/api/v1/prices")
async def list_prices(entity_id: UUID, start: str, end: str, page: int = 0, size: int = 10):
    price_manager = getattr(app.state, "price_manager", None)
    if not price_manager and not isinstance(price_manager, PriceManagerClient):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Price manager not initialized")

    start_dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S")
    end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S")
    return price_manager.query_prices(entity_id, start_dt, end_dt, page, size)

@app.post("/api/v1/prices/fetch_and_store")
#TODO: make start and end optional for datasources that don't support historical fetching
async def fetch_and_store(entity_code: Optional[str], entity_id: Optional[UUID], start: str, end: str):
    price_manager = getattr(app.state, "price_manager", None)
    if not price_manager and not isinstance(price_manager, PriceManagerClient):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Price manager not initialized")

    inserted_count = price_manager.fetch_and_store(
        entity_id,
        entity_code,
        datetime.strptime(start, "%Y-%m-%dT%H:%M:%S"),
        datetime.strptime(end, "%Y-%m-%dT%H:%M:%S"))
    return {"inserted_count": inserted_count}

# task endpoints
@app.get("/api/v1/tasks")
async def list_tasks(task_status: Optional[str], page: int = 0, size: int = 10):
    scheduler = getattr(app.state, "scheduler", None)
    if not scheduler and not isinstance(scheduler, PriceSchedulerClient):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Scheduler not initialized")

    return scheduler.get_tasks(task_status, page, size)

@app.post("/api/v1/tasks/generate")
async def generate_tasks(entity_id: Optional[UUID] = None, min_date: Optional[str] = None):
    scheduler = getattr(app.state, "scheduler", None)
    if not scheduler and not isinstance(scheduler, PriceSchedulerClient):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Scheduler not initialized")

    min_date_dt = datetime.strptime(min_date, "%Y-%m-%dT%H:%M:%S") if min_date else None
    return scheduler.generate_monthly_tasks(entity_id, min_date_dt)

# TODO: add endpoint to trigger processing of a task

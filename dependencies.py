import os
from dotenv import load_dotenv
from fastapi import Depends

from libram_database.db import Database
from price_management.service import PriceManagerService
from fundamentals_management.service import FundamentalsManagerService
from portfolio_management.service import PortfolioManagerService
from price_scheduler.service import PriceSchedulerService


async def get_db_string() -> str:
    load_dotenv()
    db_string = os.getenv("LIBRAM_DB")
    if not db_string:
        raise RuntimeError("LIBRAM_DB environment variable not set")
    return db_string


async def get_database(db_string: str = Depends(get_db_string)) -> Database:
    return Database(db_string)


async def get_price_manager_service(
    db: Database = Depends(get_database),
) -> PriceManagerService:
    return PriceManagerService(db)


async def get_fundamentals_manager_service(
    price_manager: PriceManagerService = Depends(get_price_manager_service),
    db: Database = Depends(get_database),
) -> FundamentalsManagerService:
    return FundamentalsManagerService(price_manager, db)


async def get_portfolio_manager_service(
    price_manager: PriceManagerService = Depends(get_price_manager_service),
    db: Database = Depends(get_database),
) -> PortfolioManagerService:
    return PortfolioManagerService(price_manager, db)


async def get_scheduler_service(
    price_manager: PriceManagerService = Depends(get_price_manager_service),
    db: Database = Depends(get_database),
) -> PriceSchedulerService:
    return PriceSchedulerService(price_manager, db)

from datetime import datetime
import os
from typing import Optional
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI

from libram_database.db import Database
from price_management.client import PriceManagerClient

app = FastAPI()

@app.post("/api/v1/fetch_and_store")
async def fetch_and_store(start: str, end: str, entity_code: Optional[str] = None, entity_id: Optional[UUID] = None):

    load_dotenv()
    db_string = os.getenv("LIBRAM_DB")
    if not db_string:
        print("Error: LIBRAM_DB environment variable not set")
        return

    db = Database(db_string)
    client = PriceManagerClient(db)
    inserted_count = client.fetch_and_store(
        entity_id,
        entity_code,
        datetime.strptime(start, "%Y-%m-%dT%H:%M:%S"),
        datetime.strptime(end, "%Y-%m-%dT%H:%M:%S"))
    return {"inserted_count": inserted_count}

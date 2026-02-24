"""Use PriceManagerClient to fetch/store/query financial data using command line parameters
"""

import argparse
import os

from datetime import datetime
from uuid import UUID
from dotenv import load_dotenv
from libram_database.db import Database
from price_management.client import PriceManagerClient

def main():
    parser = argparse.ArgumentParser(description="Fetch and store financial data using PriceManagerClient")
    parser.add_argument("--entity_code", required=False, help="Entity code to fetch data for")
    parser.add_argument("--entity_id", required=False, help="Entity id to fetch data for")
    # datetime range in YYYY-MM-DDTHH:MM:SS format
    parser.add_argument("--start", required=True, help="Start datetime in YYYY-MM-DDTHH:MM:SS format")
    parser.add_argument("--end", required=True, help="End datetime in YYYY-MM-DDTHH:MM:SS format")

    args = parser.parse_args()
    # get db string from environment variable or use default
    load_dotenv()
    db_string = os.getenv("LIBRAM_DB")
    if not db_string:
        print("Error: LIBRAM_DB environment variable not set")
        return

    db = Database(db_string)
    client = PriceManagerClient(db)
    inserted_count = client.fetch_and_store(
        UUID(str(args.entity_id)) if args.entity_id else None,
        args.entity_code,
        datetime.strptime(args.start, "%Y-%m-%dT%H:%M:%S"),
        datetime.strptime(args.end, "%Y-%m-%dT%H:%M:%S"))
    print(f"Inserted {inserted_count} price records for entity {args.entity_code if args.entity_code else args.entity_id} between {args.start} and {args.end}")

if __name__ == "__main__":
    main()

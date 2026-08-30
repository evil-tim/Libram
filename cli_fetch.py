"""Use PriceManagerService to fetch/store/query financial data using command line parameters
"""

import argparse
import os
from datetime import datetime
from uuid import UUID

from dotenv import load_dotenv

from libram_database.db import Database
from price_management.service import PriceManagerService


def main():
    parser = argparse.ArgumentParser(description="Fetch and store financial data using PriceManagerService")
    parser.add_argument("--entity_code", required=False, help="Entity code to fetch data for")
    parser.add_argument("--entity_id", required=False, help="Entity id to fetch data for")
    # datetime range in YYYY-MM-DDTHH:MM:SS format
    parser.add_argument("--start", required=False, help="Start datetime in YYYY-MM-DDTHH:MM:SS format")
    parser.add_argument("--end", required=False, help="End datetime in YYYY-MM-DDTHH:MM:SS format")
    parser.add_argument("--snapshot", action="store_true", help="Get price snapshot instead of historical range")
    parser.add_argument("--dry_run", action="store_true", help="Do not write data if set")

    args = parser.parse_args()
    if not args.entity_id and not args.entity_code:
        parser.error("one of --entity_id or --entity_code is required")
    if not args.snapshot and (not args.start or not args.end):
        parser.error("--start and --end are required unless --snapshot is set")
    # get db string from environment variable or use default
    load_dotenv()
    db_string = os.getenv("LIBRAM_DB")
    if not db_string:
        print("Error: LIBRAM_DB environment variable not set")
        return

    db = Database(db_string)
    client = PriceManagerService(db)
    if args.snapshot:
        inserted_count = client.fetch_snapshot_and_store(
            UUID(str(args.entity_id)) if args.entity_id else None,
            args.entity_code,
            args.dry_run)
        print(f"{datetime.now().isoformat()} : Inserted {inserted_count} price records for entity {args.entity_code if args.entity_code else args.entity_id}")
    else:
        inserted_count = client.fetch_and_store(
            UUID(str(args.entity_id)) if args.entity_id else None,
            args.entity_code,
            datetime.strptime(args.start, "%Y-%m-%dT%H:%M:%S"),
            datetime.strptime(args.end, "%Y-%m-%dT%H:%M:%S"),
            args.dry_run)
        print(f"{datetime.now().isoformat()} : Inserted {inserted_count} price records for entity {args.entity_code if args.entity_code else args.entity_id} between {args.start} and {args.end}")

if __name__ == "__main__":
    main()

"""Use PriceSchedulerClient to generate tasks using command line parameters
"""

import argparse
from datetime import datetime
import os

from dotenv import load_dotenv

from libram_database.db import Database
from price_management.client import PriceManagerClient
from price_scheduler.client import PriceSchedulerClient


def main():
    parser = argparse.ArgumentParser(description="Generate monthly tasks for missing daily prices using PriceSchedulerClient")
    parser.add_argument("--entity_id", required=False, help="Entity id to generate tasks for")
    parser.add_argument("--min_date", required=False, help="Minimum date to scan for missing prices in YYYY-MM-DDTHH:MM:SS format")

    args = parser.parse_args()
    # get db string from environment variable or use default
    load_dotenv()
    db_string = os.getenv("LIBRAM_DB")
    if not db_string:
        print("Error: LIBRAM_DB environment variable not set")
        return

    db = Database(db_string)
    price_manager_client = PriceManagerClient(db)
    price_scheduler_client = PriceSchedulerClient(price_manager_client, db)

    created_tasks = price_scheduler_client.generate_monthly_tasks(
        entity_id=args.entity_id if args.entity_id else None,
        min_date=datetime.strptime(args.min_date, "%Y-%m-%dT%H:%M:%S") if args.min_date else None
    )
    for task in created_tasks:
        print(f"Created task {task.id} for entity {task.entity_id} from {task.timestamp_start} to {task.timestamp_end} with status {task.status}")

if __name__ == "__main__":
    main()

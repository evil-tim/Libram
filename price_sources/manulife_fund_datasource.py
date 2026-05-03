"""Manulife Fund DataSource implementation.

This datasource fetches fund net asset value (NAV) data from the Manulife page.
It extends the RestJSONDatasource and implements the required methods to build the request parameters and parse the response data.
"""

import json
from typing import Any, Dict, Optional, Tuple, Iterable
from datetime import datetime

from bs4 import BeautifulSoup
from libram_types.libram_types import PriceRecord

from price_sources.html_datasource import HTMLDatasource


class ManulifeFundDataSource(HTMLDatasource):

    def build_request_params(
        self,
        entity: dict,
        start: datetime,
        end: datetime,
        config: dict,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return (self.url.format(code=entity.get("code")), None, None)

    def parse_price_data(self, data: BeautifulSoup) -> Iterable[PriceRecord]:
        # find <script id="funds-data" type="application/json">
        script_tag = data.find("script", id="funds-data")
        if not script_tag:
            raise ValueError("Expected funds data not found")

        # get the JSON content from the script tag
        try:
            json_data = script_tag.string
            if not json_data:
                raise ValueError("Funds data script tag is empty")
            raw_json_data = json.loads(json_data)
        except Exception as exc:
            raise ValueError("Failed to parse funds data JSON") from exc

        # extract price records from dataset field in the JSON
        dataset = raw_json_data.get("dataset", [])

        # convert each item in the dataset to a PriceRecord if it has the expected fields
        records = []
        for item in dataset:
            if "price" in item and "asOfDate" in item:
                records.append(
                    PriceRecord(
                        price=item.get("price"),
                        timestamp=datetime.strptime(item.get("asOfDate"), "%Y-%m-%d"),
                    )
                )
        return records

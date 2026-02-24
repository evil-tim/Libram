"""Manulife Fund DataSource implementation.

This datasource fetches fund net asset value (NAV) data from the Manulife page.
It extends the RestJSONDatasource and implements the required methods to build the request parameters and parse the response data.
"""
from typing import Any, Dict, Optional, Tuple, Union, Iterable
from datetime import datetime
from libram_types.libram_types import PriceRecord

from price_sources.rest_datasource import RestJSONDatasource


class ManulifeFundDataSource(RestJSONDatasource):

    def build_request_params(
        self,
        entity: dict,
        start: datetime,
        end: datetime,
        config: dict,
        ) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return (
            self.url.format(code=entity.get("code")),
            None,
            None)


    def parse_price_data(self, data: Union[Dict[str, Any], list]) -> Iterable[PriceRecord]:
        if not isinstance(data, list):
            raise ValueError("Expected data to be a list of price records")

        records = []
        for item in data:
            if "price" in item and "asOfDate" in item:
                records.append(PriceRecord(
                    price=item.get("price"),
                    timestamp=datetime.strptime(item.get("asOfDate"), "%Y-%m-%d"),
                ))
        return records

"""SLAMC Fund DataSource implementation.

This datasource fetches fund net asset value (NAV) data from the SLAMC API.
It extends the RestJSONDatasource and implements the required methods to build the request parameters and parse the response data.
"""
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Optional, Union

from libram_types.libram_types import PriceRecord
from price_sources.rest_datasource import RestJSONDatasource


class SLAMCFundDataSource(RestJSONDatasource):

    def build_request_params(
        self,
        entity: dict,
        start: datetime,
        end: datetime,
        config: dict,
        ) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        # For SLAMC, we need to send the fund code and date range as query parameters.
        date_from = start.strftime("%Y-%m-%dT16:00:00.000Z")
        date_to = end.strftime("%Y-%m-%dT16:00:00.000Z")
        return (
            None,
            {
                "version" : "1",
                "language" : "en-us"
            },
            {
                "fundCode": entity.get("code"),
                "dateFrom": date_from,
                "dateTo": date_to,
            })


    def parse_price_data(self, data: Union[dict[str, Any], list]) -> Iterable[PriceRecord]:
        if not isinstance(data, list):
            raise TypeError("Expected data to be a list of price records")

        records = []
        for item in data:
            records.append(PriceRecord(
                price=item.get("fundNetVal"),
                timestamp=datetime.strptime(item.get("fundValDate"), "%Y-%m-%d"),
            ))
        return records

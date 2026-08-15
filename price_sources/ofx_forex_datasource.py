"""OFX Forex DataSource implementation.

This datasource fetches forex price data from the OFX API.
It extends the RestJSONDatasource and implements the required methods to build the request parameters and parse the response data.
"""
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Optional, Union

from libram_types.libram_types import PriceRecord
from price_sources.rest_datasource import RestJSONDatasource


class OFXForexDataSource(RestJSONDatasource):

    def build_request_params(
        self,
        entity: dict,
        start: datetime,
        end: datetime,
        config: dict,
        ) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        base = config.get("base")
        currency = config.get("currency")
        start_epoch = int(start.timestamp() * 1000)
        end_epoch = int(end.timestamp() * 1000)
        return (
            self.url.format(base=base, currency=currency, start_epoch=start_epoch, end_epoch=end_epoch),
            {
                "DecimalPlaces": 6,
                "ReportingInterval": "daily",
                "format": "json"
            },
            None)

    def parse_price_data(self, data: Union[dict[str, Any], list]) -> Iterable[PriceRecord]:
        if not isinstance(data, dict):
            raise ValueError("Expected data to be a base object containing price records")

        # unwrap the nested HistoricalPoints field
        historical_points = data.get("HistoricalPoints")
        if not historical_points:
            raise ValueError("Expected 'HistoricalPoints' field in response data")

        records = []
        for item in historical_points:
            date_epoch = item.get("PointInTime")
            date = datetime.fromtimestamp(date_epoch / 1000)
            price = item.get("InterbankRate")
            records.append(PriceRecord(
                price=price,
                timestamp=date,
            ))
        return records

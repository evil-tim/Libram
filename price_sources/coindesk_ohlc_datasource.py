"""Coindesk OHLC DataSource implementation.

This datasource fetches price data from the Coindesk API.
It extends the RestJSONDatasource and implements the required methods to build the request parameters and parse the response data.
"""
from typing import Any, Dict, Optional, Tuple, Union, Iterable
from datetime import datetime, timedelta
from libram_types.libram_types import PriceRecord

from price_sources.rest_datasource import RestJSONDatasource


class CoindeskOHLCDataSource(RestJSONDatasource):

    def build_request_params(
        self,
        entity: dict,
        start: datetime,
        end: datetime,
        config: dict,
        ) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:

        market = config.get("market")
        instrument = config.get("instrument")
        aggregate = config.get("aggregate", 1)
        fill = str(config.get("fill", True)).lower()
        apply_mapping = str(config.get("apply_mapping", True)).lower()
        to_ts = int(end.timestamp())
        # daily API - convert end to start as number of days to be used as limit
        limit = int((end - start).total_seconds() / (24 * 3600)) + 1
        api_key = config.get("api_key")

        return (
            None,
            {
                "market": market,
                "instrument": instrument,
                "limit": limit,
                "aggregate": aggregate,
                "fill": fill,
                "apply_mapping": apply_mapping,
                "response_format": "JSON",
                "to_ts": to_ts,
                "api_key": api_key
            },
            None)

    def parse_price_data(self, data: Union[Dict[str, Any], list]) -> Iterable[PriceRecord]:
        if not isinstance(data, Dict):
            raise ValueError("Expected data to be a base object containing price records")

        data_points = data.get("Data")
        if not data_points:
            raise ValueError("Expected 'Data' field in response data")

        records = []
        for item in data_points:
            open_price = item.get("OPEN")
            high_price = item.get("HIGH")
            low_price = item.get("LOW")
            close_price = item.get("CLOSE")
            start = datetime.fromtimestamp(item.get("TIMESTAMP"))
            end = start + timedelta(days=1)  # Assuming daily data, end is start + 1 day
            records.append(PriceRecord(
                 open=open_price,
                 high=high_price,
                 low=low_price,
                 close=close_price,
                 timestamp_start=start,
                 timestamp_end=end
            ))
        return records

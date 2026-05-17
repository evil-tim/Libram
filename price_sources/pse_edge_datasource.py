"""PSE Edge Fund DataSource implementation.

This datasource fetches fund OHLC data from the PSE Edge page.
It extends the RestJSONDatasource and implements the required methods to build the request parameters and parse the response data.
"""
from typing import Any, Dict, Optional, Tuple, Union, Iterable
from datetime import datetime, timedelta
from libram_types.libram_types import PriceRecord

from price_sources.rest_datasource import RestJSONDatasource


class PSEEdgeDataSource(RestJSONDatasource):

    def build_request_params(
        self,
        entity: dict,
        start: datetime,
        end: datetime,
        config: dict,
        ) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        # input date is start inclusive and end exclusive, but the datasource expects end to be inclusive
        # subtract 1 day from end date to make it inclusive for the datasource
        return (
            None,
            None,
            {
                "cmpy_id": config.get("cmpy_id"),
                "security_id": config.get("security_id"),
                "startDate": start.strftime("%m-%d-%Y"),
                "endDate": (end - timedelta(days=1)).strftime("%m-%d-%Y")
            })


    def parse_price_data(self, data: Union[Dict[str, Any], list]) -> Iterable[PriceRecord]:
        if not isinstance(data, Dict):
            raise ValueError("Expected data to be a base object containing price records")

        # unwrap the nested chartData field
        chart_data = data.get("chartData")
        if not chart_data:
            raise ValueError("Expected 'chartData' field in response data")

        # validate that chartData is a list of price records
        if not isinstance(chart_data, list):
            raise ValueError("Expected 'chartData' to be a list of price records")

        records = []
        for item in chart_data:
            # parse the date string in the format "Jan 01, 2020 00:00:00"
            date_str = item.get("CHART_DATE")
            date_from = datetime.strptime(date_str, "%b %d, %Y %H:%M:%S")
            # generate date_to as next day exclusive
            date_to = date_from + timedelta(days=1)

            records.append(PriceRecord(
                open=item.get("OPEN"),
                high=item.get("HIGH"),
                low=item.get("LOW"),
                close=item.get("CLOSE"),
                timestamp_start=date_from,
                timestamp_end=date_to
            ))
        return records

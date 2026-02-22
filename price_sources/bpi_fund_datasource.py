"""BPI Fund DataSource implementation.

This datasource fetches fund net asset value (NAV) data from the BPI page.
It extends the RestJSONDatasource and implements the required methods to build the request parameters and parse the response data.
"""
import json
from typing import Any, Dict, Optional, Tuple, Union, Iterable
from datetime import datetime
from price_management.types import PriceRecord

from price_sources.rest_datasource import RestJSONDatasource


class BPIFundDataSource(RestJSONDatasource):

    def build_request_params(
        self,
        entity: dict,
        start: datetime,
        end: datetime,
        config: dict,
        ) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return (
            None,
            {
                "fundCode": entity.get("code"),
                "startDate": start.strftime("%d/%m/%Y"),
                "endDate": end.strftime("%d/%m/%Y"),
            },
            None)


    def parse_price_data(self, data: Union[Dict[str, Any], list]) -> Iterable[PriceRecord]:
        if not isinstance(data, Dict):
            raise ValueError("Expected data to be a base object containing price records")

        # unwrap the nested fundData string
        fund_data_str = data.get("fundData")
        if not fund_data_str:
            raise ValueError("Expected 'fundData' field in response data")

        # parse the fund_data_str string as JSON
        fund_data = json.loads(fund_data_str)
        if not isinstance(fund_data, Dict):
            raise ValueError("Expected 'fundData' to be a JSON object containing price records")

        # unwrap the fundDataHistory list which contains the actual price records
        fund_data_history = fund_data.get("fundDataHistory")
        if not fund_data_history or not isinstance(fund_data_history, list):
            raise ValueError("Expected 'fundDataHistory' field in fundData to be a list of price records")

        records = []
        for item in fund_data_history:
            records.append(PriceRecord(
                price=item.get("navpuValue"),
                timestamp=datetime.fromtimestamp(item.get("date") / 1000),
            ))
        return records

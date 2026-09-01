"""An abstract JSON REST datasource for fetching prices.

The instance is initialized with a config dict. Expected config keys:
- "url" (required): the endpoint URL
- "method": HTTP method (default: "GET")
- "headers": dict of headers (default: {})
- "timeout": request timeout seconds (default: 10)
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Optional, Union

import requests

from libram_types.libram_types import PriceRecord
from price_management import BaseDatasource
from price_management.datasource import UnsupportedDatasourceOperationError


class RestJSONDatasource(BaseDatasource):
    """Download JSON data from a configurable REST endpoint.

    The class focuses on fetching JSON responses; it raises on HTTP errors
    and on invalid JSON.

    Implementations should override `build_request_params` to build the query
    parameters based on the entity and time range.
    """

    def __init__(self, config: dict):
        super().__init__(config)

        # Specific config keys for the REST datasource
        self.url = str(self.config.get("url"))
        if not self.url:
            raise ValueError("config must include 'url'")
        self.method = (self.config.get("method") or "GET").upper()
        self.headers: dict[str, str] = self.config.get("headers") or {}
        self.timeout: int = self.config.get("timeout", 10)

    def fetch(
        self,
        url: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        request_params: Optional[dict[str, Any]] = None,
        request_body: Optional[dict[str, Any]] = None,
    ) -> Union[dict[str, Any], list]:
        """Perform the HTTP request and return parsed JSON.

        Args:
            url: Optional override for the URL (defaults to self.url).
            params: Query parameters to send with the request.
            json_body: JSON body for methods like POST/PUT.

        Returns:
            The parsed JSON response (typically a list or dict).

        Raises:
            requests.HTTPError for non-success status codes.
            ValueError if the response body is not valid JSON.
        """
        resp = requests.request(
            method=self.method,
            url=url or self.url,
            headers=headers,
            params=request_params,
            json=request_body,
            timeout=self.timeout,
        )

        resp.raise_for_status()

        try:
            return resp.json()
        except ValueError as exc:
            raise ValueError("Response did not contain valid JSON") from exc

    def build_request_params(
        self,
        entity: dict,
        start: datetime,
        end: datetime,
        config: dict,
    ) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """Build the url, query parameters and request body params for the request based on the entity and time range
        as well as the current instance's config.

        Subclasses should override this method to construct the request parameters for the specific REST API being used.
        """
        raise UnsupportedDatasourceOperationError(
            "This datasource does not support historical price fetching."
        )

    def parse_price_data(
        self, data: Union[dict[str, Any], list]
    ) -> Iterable[PriceRecord]:
        """Parse the raw JSON data returned by `fetch` into an iterable of `PriceRecord`.

        Subclasses should override this method to extract historical prices from the JSON response.
        """
        raise UnsupportedDatasourceOperationError(
            "This datasource does not support historical price fetching."
        )

    def build_request_params_snapshot(
        self,
        entity: dict,
        config: dict,
    ) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """Build the url, query parameters and request body params for the request based on the entity
        as well as the current instance's config.

        Subclasses should override this method to construct the request parameters for the specific REST API being used.
        """
        raise UnsupportedDatasourceOperationError(
            "This datasource does not support snapshot price fetching."
        )

    def parse_price_data_snapshot(
        self, data: Union[dict[str, Any], list]
    ) -> PriceRecord:
        """Parse the raw JSON data returned by `fetch` into a single `PriceRecord`.

        Subclasses should override this method to extract the current price from the JSON response.
        """
        raise UnsupportedDatasourceOperationError(
            "This datasource does not support snapshot price fetching."
        )

    def build_headers(
        self,
        base_headers: dict[str, str],
        entity: dict,
        config: dict,
    ) -> dict[str, str]:
        """Build the request headers based on the base headers, entity, and config.

        By default, this just returns the base headers, but implementations can override
        this to add dynamic headers (e.g. for authentication).
        """
        return base_headers

    def fetch_prices(
        self, entity: dict, start: datetime, end: datetime
    ) -> Iterable[PriceRecord]:
        url, request_params, request_body = self.build_request_params(
            entity=entity, start=start, end=end, config=self.config
        )

        headers = self.build_headers(self.headers, entity=entity, config=self.config)

        data = self.fetch(
            url=url,
            headers=headers,
            request_params=request_params,
            request_body=request_body,
        )

        return self.parse_price_data(data)

    def fetch_price(self, entity: dict) -> PriceRecord:
        url, request_params, request_body = self.build_request_params_snapshot(
            entity=entity, config=self.config
        )

        headers = self.build_headers(self.headers, entity=entity, config=self.config)

        data = self.fetch(
            url=url,
            headers=headers,
            request_params=request_params,
            request_body=request_body,
        )

        return self.parse_price_data_snapshot(data)

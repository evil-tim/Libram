"""An abstract JSON REST datasource for fetching prices.

The instance is initialized with a config dict. Expected config keys:
- "url" (required): the endpoint URL
- "method": HTTP method (default: "GET")
- "headers": dict of headers (default: {})
- "timeout": request timeout seconds (default: 10)
"""
from abc import abstractmethod
from typing import Any, Dict, Optional, Tuple, Union, Iterable
from datetime import datetime

import requests

from libram_types.libram_types import PriceRecord
from price_management import BaseDatasource


class RestJSONDatasource(BaseDatasource):
    """Download JSON data from a configurable REST endpoint.

    The class focuses on fetching JSON responses; it raises on HTTP errors
    and on invalid JSON.

    Implementations should override `build_request_params` to build the query
    parameters based on the entity and time range.
    """

    def __init__(self, config: dict):
        # The combined datasource/entity config is passed in as `config`.
        self.config = config or {}

        # Specific config keys for the REST datasource
        self.url = str(config.get("url"))
        if not self.url:
            raise ValueError("config must include 'url'")
        self.method = (config.get("method") or "GET").upper()
        self.headers: Dict[str, str] = config.get("headers") or {}
        self.timeout: int = config.get("timeout", 10)

    def fetch(
        self,
        url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        request_params: Optional[Dict[str, Any]] = None,
        request_body: Optional[Dict[str, Any]] = None,
    ) -> Union[Dict[str, Any], list]:
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

    @abstractmethod
    def build_request_params(
        self,
        entity: dict,
        start: datetime,
        end: datetime,
        config: dict,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Build the url, query parameters and request body params for the request based on the entity and time range
        as well as the current instance's config.
        """
        raise NotImplementedError()

    @abstractmethod
    def parse_price_data(self, data: Union[Dict[str, Any], list]) -> Iterable[PriceRecord]:
        """Parse the raw JSON data returned by `fetch` into an iterable of `PriceRecord`.
        """
        raise NotImplementedError()

    def build_headers(
        self,
        base_headers: Dict[str, str],
        entity: dict,
        config: dict,
    ) -> Dict[str, str]:
        """Build the request headers based on the base headers, entity, and config.

        By default, this just returns the base headers, but implementations can override
        this to add dynamic headers (e.g. for authentication).
        """
        return base_headers

    def fetch_prices(self, entity: dict, start: datetime, end: datetime) -> Iterable[PriceRecord]:
        url, request_params, request_body = self.build_request_params(
            entity=entity,
            start=start,
            end=end,
            config=self.config)

        headers = self.build_headers(
            self.headers,
            entity=entity,
            config=self.config)

        data = self.fetch(
            url=url,
            headers=headers,
            request_params=request_params,
            request_body=request_body)

        return self.parse_price_data(data)

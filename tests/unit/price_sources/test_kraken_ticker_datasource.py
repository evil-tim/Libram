"""Unit tests for the Kraken ticker datasource."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from libram_types.libram_types import PriceRecord
from price_management.datasource import UnsupportedDatasourceOperationError
from price_sources.kraken_ticker_datasource import KrakenTickerDataSource

CONFIG = {
    "url": "https://api.kraken.com/0/public/Ticker",
    "method": "GET",
    "pair": "BTC/USD",
}

TICKER_PAYLOAD = {
    "error": [],
    "result": {
        "BTC/USD": {
            "a": ["30300.10000", "1", "1.000"],
            "b": ["30300.00000", "1", "1.000"],
            "c": ["30303.20000", "0.00067643"],
            "v": ["4083.67001100", "4412.73601799"],
            "p": ["30706.77771", "30689.13205"],
            "t": [34619, 38907],
            "l": ["29868.30000", "29868.30000"],
            "h": ["31631.00000", "31631.00000"],
            "o": "30502.80000",
        }
    },
}


def make_response(payload):
    response = Mock()
    response.json.return_value = payload
    return response


def test_kraken_ticker_request_and_parse():
    datasource = KrakenTickerDataSource(CONFIG)

    with patch("price_sources.rest_datasource.requests.request") as request_mock:
        request_mock.return_value = make_response(TICKER_PAYLOAD)
        record = datasource.fetch_price({"code": "BTC-USD"})

    request_mock.assert_called_once()
    kwargs = request_mock.call_args.kwargs
    assert kwargs["method"] == "GET"
    assert kwargs["url"] == "https://api.kraken.com/0/public/Ticker"
    assert kwargs["params"] == {"pair": "BTC/USD", "assetVersion": 1}

    assert isinstance(record, PriceRecord)
    assert record.price == Decimal("30303.20000")
    assert record.timestamp is not None
    assert record.timestamp.tzinfo is not None
    assert record.timestamp <= datetime.now(UTC)


def test_kraken_ticker_rejects_api_error():
    datasource = KrakenTickerDataSource(CONFIG)
    payload = {"error": ["EGeneral:Invalid arguments"], "result": {}}

    with patch("price_sources.rest_datasource.requests.request") as request_mock:
        request_mock.return_value = make_response(payload)
        with pytest.raises(ValueError, match="Kraken API error"):
            datasource.fetch_price({"code": "BTC-USD"})


@pytest.mark.parametrize(
    "payload",
    [
        {"error": [], "result": {}},
        {"error": []},
        {"error": [], "result": {"BTC/USD": {}, "ETH/USD": {"c": ["1.0", "1"]}}},
        {"error": [], "result": {"BTC/USD": {"a": ["1.0", "1", "1"]}}},
        {"error": [], "result": {"BTC/USD": {"c": []}}},
        {"error": [], "result": {"BTC/USD": {"c": ["not-a-number", "1"]}}},
        ["not", "an", "object"],
    ],
)
def test_kraken_ticker_rejects_bad_payloads(payload):
    datasource = KrakenTickerDataSource(CONFIG)

    with patch("price_sources.rest_datasource.requests.request") as request_mock:
        request_mock.return_value = make_response(payload)
        with pytest.raises((TypeError, ValueError)):
            datasource.fetch_price({"code": "BTC-USD"})


def test_kraken_ticker_requires_pair_config():
    with pytest.raises(ValueError, match="pair"):
        KrakenTickerDataSource({"url": "https://api.kraken.com/0/public/Ticker"})


def test_kraken_ticker_does_not_support_historical():
    datasource = KrakenTickerDataSource(CONFIG)

    with pytest.raises(UnsupportedDatasourceOperationError):
        datasource.fetch_prices(
            {"code": "BTC-USD"},
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        )

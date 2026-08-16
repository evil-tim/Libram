import json
import time
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from bs4 import BeautifulSoup

from libram_types import PriceRecord
from price_sources.bpi_fund_datasource import BPIFundDataSource
from price_sources.coindesk_ohlc_datasource import CoindeskOHLCDataSource
from price_sources.html_datasource import HTMLDatasource
from price_sources.manulife_fund_datasource import ManulifeFundDataSource
from price_sources.ofx_forex_datasource import OFXForexDataSource
from price_sources.pse_edge_datasource import PSEEdgeDataSource
from price_sources.rest_datasource import RestJSONDatasource
from price_sources.slamc_fund_datasource import SLAMCFundDataSource

START = datetime(2024, 1, 2, 12, tzinfo=UTC)
END = datetime(2024, 1, 5, 12, tzinfo=UTC)
ENTITY = {"code": "FUND-1"}


def utc_datetime(epoch: float) -> datetime:
    """Normalize an epoch to the naive UTC values returned by datasource APIs."""
    return datetime.fromtimestamp(epoch, UTC).replace(tzinfo=None)


def local_datetime(epoch: float) -> datetime:
    """Express an epoch in the host local timezone without mutating process state."""
    offset = timedelta(seconds=-time.timezone)
    return datetime.fromtimestamp(epoch, timezone(offset)).replace(tzinfo=None)


def records(datasource, payload):
    return list(datasource.parse_price_data(payload))


def test_rest_fetch_passes_request_and_returns_json():
    datasource = _ConcreteRESTDatasource({"url": "https://example.test", "timeout": 7})
    response = Mock()
    response.json.return_value = {"ok": True}

    with patch(
        "price_sources.rest_datasource.requests.request", return_value=response
    ) as request:
        result = datasource.fetch(
            url="https://override.test",
            headers={"X-Test": "yes"},
            request_params={"page": 2},
            request_body={"query": "value"},
        )

    assert result == {"ok": True}
    request.assert_called_once_with(
        method="GET",
        url="https://override.test",
        headers={"X-Test": "yes"},
        params={"page": 2},
        json={"query": "value"},
        timeout=7,
    )
    response.raise_for_status.assert_called_once_with()


def test_rest_fetch_rejects_invalid_json():
    datasource = _ConcreteRESTDatasource({"url": "https://example.test"})
    response = Mock()
    response.json.side_effect = ValueError("not json")

    with (
        patch("price_sources.rest_datasource.requests.request", return_value=response),
        pytest.raises(ValueError, match="valid JSON"),
    ):
        datasource.fetch()


def test_rest_fetch_prices_builds_and_parses_without_live_io():
    datasource = _ConcreteRESTDatasource(
        {
            "url": "https://example.test/configured",
            "method": "post",
            "headers": {"A": "B"},
            "timeout": 23,
        }
    )
    response = Mock()
    response.json.return_value = {"value": 4}

    with (
        patch.object(
            datasource,
            "build_request_params",
            return_value=(
                "https://example.test/generated",
                {"q": 1},
                {"body": "value"},
            ),
        ) as build,
        patch(
            "price_sources.rest_datasource.requests.request", return_value=response
        ) as request,
    ):
        result = datasource.fetch_prices(ENTITY, START, END)

    assert result == [PriceRecord(price=Decimal(4))]
    build.assert_called_once_with(
        entity=ENTITY, start=START, end=END, config=datasource.config
    )
    request.assert_called_once_with(
        method="POST",
        url="https://example.test/generated",
        headers={"A": "B"},
        params={"q": 1},
        json={"body": "value"},
        timeout=23,
    )
    response.raise_for_status.assert_called_once_with()


def test_html_fetch_parses_response_content():
    datasource = _ConcreteHTMLDatasource(
        {"url": "https://example.test", "method": "post"}
    )
    response = Mock(content=b"<html><body>ok</body></html>")

    with patch(
        "price_sources.html_datasource.requests.request", return_value=response
    ) as request:
        result = datasource.fetch(headers={"Accept": "text/html"})

    assert result.title is None
    assert result.body.get_text(strip=True) == "ok"
    request.assert_called_once_with(
        method="POST",
        url="https://example.test",
        headers={"Accept": "text/html"},
        params=None,
        json=None,
        timeout=10,
    )


def test_pse_request_and_parse():
    datasource = PSEEdgeDataSource(
        {"url": "https://pse.test", "cmpy_id": 11, "security_id": 22}
    )
    assert datasource.build_request_params(ENTITY, START, END, datasource.config) == (
        None,
        None,
        {
            "cmpy_id": 11,
            "security_id": 22,
            "startDate": "01-02-2024",
            "endDate": "01-04-2024",
        },
    )
    payload = {
        "chartData": [
            {
                "CHART_DATE": "Jan 02, 2024 00:00:00",
                "OPEN": 1,
                "HIGH": 2,
                "LOW": 0,
                "CLOSE": 1.5,
            }
        ]
    }
    assert records(datasource, payload) == [
        PriceRecord(
            open=1,
            high=2,
            low=0,
            close=1.5,
            timestamp_start=utc_datetime(1704153600),
            timestamp_end=utc_datetime(1704240000),
        )
    ]


@pytest.mark.parametrize("payload", [[], {"chartData": []}, {"chartData": None}])
def test_pse_rejects_empty_payloads(payload):
    datasource = PSEEdgeDataSource({"url": "https://pse.test"})
    with pytest.raises((TypeError, ValueError)):
        records(datasource, payload)


def test_coindesk_request_and_parse():
    datasource = CoindeskOHLCDataSource(
        {
            "url": "https://coin.test",
            "market": "cadli",
            "instrument": "BTC-USD",
            "aggregate": 2,
            "fill": False,
            "api_key": "key",
        }
    )
    params = datasource.build_request_params(ENTITY, START, END, datasource.config)
    assert params[0] is None and params[2] is None
    assert params[1] == {
        "market": "cadli",
        "instrument": "BTC-USD",
        "limit": 4,
        "aggregate": 2,
        "fill": "false",
        "apply_mapping": "true",
        "response_format": "JSON",
        "to_ts": int(END.timestamp()),
        "api_key": "key",
    }
    payload = {
        "Data": [
            {
                "TIMESTAMP": 1704153600,
                "OPEN": 10,
                "HIGH": 12,
                "LOW": 9,
                "CLOSE": 11,
            }
        ]
    }
    assert records(datasource, payload) == [
        PriceRecord(
            open=10,
            high=12,
            low=9,
            close=11,
            timestamp_start=local_datetime(1704153600),
            timestamp_end=local_datetime(1704240000),
        )
    ]


@pytest.mark.parametrize("payload", [[], {}, {"Data": []}, {"Data": None}])
def test_coindesk_rejects_missing_data(payload):
    with pytest.raises((TypeError, ValueError)):
        records(CoindeskOHLCDataSource({"url": "https://coin.test"}), payload)


def test_ofx_request_and_parse():
    datasource = OFXForexDataSource(
        {
            "url": "https://ofx.test/{base}/{currency}/{start_epoch}/{end_epoch}",
            "base": "USD",
            "currency": "PHP",
        }
    )
    url, params, body = datasource.build_request_params(
        ENTITY, START, END, datasource.config
    )
    assert (
        url
        == f"https://ofx.test/USD/PHP/{int(START.timestamp() * 1000)}/{int(END.timestamp() * 1000)}"
    )
    assert params == {
        "DecimalPlaces": 6,
        "ReportingInterval": "daily",
        "format": "json",
    }
    assert body is None
    assert records(
        datasource,
        {"HistoricalPoints": [{"PointInTime": 1704196800000, "InterbankRate": 55.5}]},
    ) == [PriceRecord(price=55.5, timestamp=local_datetime(1704196800))]


@pytest.mark.parametrize(
    "payload", [[], {}, {"HistoricalPoints": []}, {"HistoricalPoints": None}]
)
def test_ofx_rejects_missing_points(payload):
    with pytest.raises(ValueError):
        records(OFXForexDataSource({"url": "https://ofx.test"}), payload)


def test_bpi_request_and_parse_with_partial_record():
    datasource = BPIFundDataSource({"url": "https://bpi.test"})
    assert datasource.build_request_params(ENTITY, START, END, datasource.config) == (
        None,
        {"fundCode": "FUND-1", "startDate": "02/01/2024", "endDate": "05/01/2024"},
        None,
    )
    payload = {
        "fundData": json.dumps(
            {
                "fundDataHistory": [
                    {"date": 1704196800000, "navpuValue": "1.25"},
                    {"date": 1704283200000},
                ]
            }
        )
    }
    assert records(datasource, payload) == [
        PriceRecord(price="1.25", timestamp=local_datetime(1704196800)),
        PriceRecord(price=None, timestamp=local_datetime(1704283200)),
    ]


@pytest.mark.parametrize(
    "payload", [[], {}, {"fundData": "{}"}, {"fundData": "not-json"}]
)
def test_bpi_rejects_malformed_payload(payload):
    with pytest.raises((TypeError, ValueError, json.JSONDecodeError)):
        records(BPIFundDataSource({"url": "https://bpi.test"}), payload)


def test_manulife_request_and_parse_skips_partial_items():
    datasource = ManulifeFundDataSource({"url": "https://manu.test/{code}"})
    assert datasource.build_request_params(ENTITY, START, END, datasource.config) == (
        "https://manu.test/FUND-1",
        None,
        None,
    )
    soup = BeautifulSoup(
        "<script id='funds-data' type='application/json'>"
        + json.dumps(
            {"dataset": [{"price": 2.5, "asOfDate": "2024-01-02"}, {"price": 3}]}
        )
        + "</script>",
        "html.parser",
    )
    assert records(datasource, soup) == [
        PriceRecord(price=2.5, timestamp=utc_datetime(1704153600))
    ]


@pytest.mark.parametrize(
    "html",
    [
        "<p>missing</p>",
        "<script id='funds-data'></script>",
        "<script id='funds-data'>{</script>",
    ],
)
def test_manulife_rejects_missing_or_malformed_script(html):
    with pytest.raises(ValueError):
        records(
            ManulifeFundDataSource({"url": "https://manu.test"}),
            BeautifulSoup(html, "html.parser"),
        )


def test_slamc_request_and_parse_with_missing_fields():
    datasource = SLAMCFundDataSource({"url": "https://slamc.test"})
    assert datasource.build_request_params(ENTITY, START, END, datasource.config) == (
        None,
        {"version": "1", "language": "en-us"},
        {
            "fundCode": "FUND-1",
            "dateFrom": "2024-01-02T16:00:00.000Z",
            "dateTo": "2024-01-05T16:00:00.000Z",
        },
    )
    assert records(
        datasource,
        [
            {"fundNetVal": 4.2, "fundValDate": "2024-01-02"},
            {"fundValDate": "2024-01-03"},
        ],
    ) == [
        PriceRecord(price=4.2, timestamp=utc_datetime(1704153600)),
        PriceRecord(price=None, timestamp=utc_datetime(1704240000)),
    ]


@pytest.mark.parametrize("payload", [{}, {"fundNetVal": 1}, [None]])
def test_slamc_rejects_non_list_payload(payload):
    with pytest.raises((TypeError, AttributeError)):
        records(SLAMCFundDataSource({"url": "https://slamc.test"}), payload)


class _ConcreteRESTDatasource(RestJSONDatasource):
    def build_request_params(self, entity, start, end, config):
        return None, None, None

    def parse_price_data(self, data):
        return [PriceRecord(price=Decimal(str(data["value"])))]


class _ConcreteHTMLDatasource(HTMLDatasource):
    def build_request_params(self, entity, start, end, config):
        return None, None, None

    def parse_price_data(self, data):
        return []

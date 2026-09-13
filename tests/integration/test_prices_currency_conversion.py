"""HTTP-level tests for the output currency option on ``GET /api/v1/prices``.

Route behaviour belongs in this boundary per ``AGENTS.md``. The database is
replaced with dependency overrides rather than a real PostgreSQL instance, so
these tests stay deterministic and need no external services.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import server
from currency_conversion.service import CurrencyConversionService
from dependencies import get_currency_conversion_service, get_price_manager_service
from libram_types.libram_types import PriceRecord

OBSERVED_AT = datetime(2026, 1, 1, 23, 59, tzinfo=UTC)


class FakeDatabase:
    """Serves the entity and rate reads that the route and service perform."""

    def __init__(self, entities, rates=None):
        self.entities = {str(key): value for key, value in entities.items()}
        self.rates = {str(key): value for key, value in (rates or {}).items()}

    def get_entity_by_id_raw(self, entity_id):
        return self.entities.get(str(entity_id))

    def get_price_at_or_before(self, entity_id, target):
        return self.rates.get(str(entity_id))

    def query_daily_last_price(self, entity_id, start, end):
        return []


class FakePriceManager:
    """Returns a fixed page of records for any query."""

    def __init__(self, db, records):
        self.db = db
        self.records = records

    def query_prices(self, entity_id, start, end, page=0, size=10):
        return self.records


def entity(code, currency_id=None, timezone="UTC") -> dict:
    return {
        "id": uuid4(),
        "code": code,
        "currency_id": currency_id,
        "timezone": timezone,
    }


@pytest.fixture
def client():
    """A TestClient whose services are fakes, overridden per test."""

    def build(entities, records, rates=None):
        db = FakeDatabase(entities, rates)
        server.app.dependency_overrides[get_price_manager_service] = lambda: (
            FakePriceManager(db, records)
        )
        server.app.dependency_overrides[get_currency_conversion_service] = lambda: (
            CurrencyConversionService(db)
        )
        return TestClient(server.app)

    yield build
    server.app.dependency_overrides.clear()


def url(entity_id, **params) -> str:
    query = {
        "entity_id": str(entity_id),
        "start": "2026-01-01T00:00:00",
        "end": "2026-01-02T00:00:00",
    }
    query.update({key: value for key, value in params.items() if value is not None})
    return "/api/v1/prices?" + "&".join(
        f"{key}={value}" for key, value in query.items()
    )


def usd_denominated_btc():
    """A USD-denominated BTC entity plus the USD currency entity it is priced in."""
    usd = entity("USD", currency_id=None)
    btc = entity("BTC", currency_id=usd["id"])
    return usd, btc


def test_omitting_the_output_currency_leaves_the_response_unchanged(client):
    usd, btc = usd_denominated_btc()
    records = [PriceRecord(price=Decimal("1200.19"), timestamp=OBSERVED_AT)]
    response = client({usd["id"]: usd, btc["id"]: btc}, records).get(url(btc["id"]))

    assert response.status_code == 200
    (record,) = response.json()
    assert record["price"] == 1200.19
    assert "currency" not in record
    assert "conversion" not in record


def test_php_output_converts_the_record_and_reports_the_arithmetic(client):
    usd, btc = usd_denominated_btc()
    records = [PriceRecord(price=Decimal("1200.19"), timestamp=OBSERVED_AT)]
    response = client(
        {usd["id"]: usd, btc["id"]: btc}, records, {usd["id"]: Decimal(57)}
    ).get(url(btc["id"], quote_currency_id="PHP"))

    assert response.status_code == 200
    (record,) = response.json()
    assert record["price"] == 68410.83
    assert record["currency"] == "PHP"
    assert record["conversion"] == {"from": "USD", "rate": 57.0, "direction": "direct"}


def test_an_entity_already_in_the_requested_currency_is_not_converted(client):
    usd, btc = usd_denominated_btc()
    records = [PriceRecord(price=Decimal("1200.19"), timestamp=OBSERVED_AT)]
    response = client({usd["id"]: usd, btc["id"]: btc}, records).get(
        url(btc["id"], quote_currency_id=str(usd["id"]))
    )

    assert response.status_code == 200
    (record,) = response.json()
    assert record["price"] == 1200.19
    assert record["currency"] == "USD"
    assert record["conversion"] is None


def test_a_currency_code_is_rejected_as_a_parameter(client):
    usd, btc = usd_denominated_btc()
    response = client({usd["id"]: usd, btc["id"]: btc}, []).get(
        url(btc["id"], quote_currency_id="USD")
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_value"


def test_an_unknown_currency_entity_is_a_404(client):
    usd, btc = usd_denominated_btc()
    unknown = uuid4()
    response = client({usd["id"]: usd, btc["id"]: btc}, []).get(
        url(btc["id"], quote_currency_id=str(unknown))
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "error": "entity_not_found",
        "entity_id": str(unknown),
    }


def test_a_two_hop_pair_reports_no_path(client):
    usdc, wbtc = entity("USDC", currency_id=None), entity("WBTC", currency_id=None)
    usd = entity("USD", currency_id=None)
    wbtc["currency_id"] = usdc["id"]
    records = [PriceRecord(price=Decimal("1200.19"), timestamp=OBSERVED_AT)]
    response = client(
        {usdc["id"]: usdc, usd["id"]: usd, wbtc["id"]: wbtc}, records
    ).get(url(wbtc["id"], quote_currency_id=str(usd["id"])))

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error": "fx_no_path",
        "from": "USDC",
        "to": "USD",
    }


def test_a_missing_rate_fails_the_request(client):
    usd, btc = usd_denominated_btc()
    records = [PriceRecord(price=Decimal("1200.19"), timestamp=OBSERVED_AT)]
    response = client({usd["id"]: usd, btc["id"]: btc}, records).get(
        url(btc["id"], quote_currency_id="PHP")
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "fx_rate_unavailable"
    assert str(usd["id"]) in detail["reason"]


def test_an_ohlc_bar_converts_every_monetary_field(client):
    usd, btc = usd_denominated_btc()
    records = [
        PriceRecord(
            open=Decimal(1140),
            high=Decimal(1210),
            low=Decimal(1135),
            close=Decimal("1200.19"),
            timestamp_start=datetime(2026, 1, 1, tzinfo=UTC),
            timestamp_end=datetime(2026, 1, 2, tzinfo=UTC),
        )
    ]
    response = client(
        {usd["id"]: usd, btc["id"]: btc}, records, {usd["id"]: Decimal(57)}
    ).get(url(btc["id"], quote_currency_id="PHP"))

    assert response.status_code == 200
    (record,) = response.json()
    assert record["open"] == 64980.0
    assert record["high"] == 68970.0
    assert record["low"] == 64695.0
    assert record["close"] == 68410.83
    assert record["low"] <= min(record["open"], record["close"])
    assert record["high"] >= max(record["open"], record["close"])

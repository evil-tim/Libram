"""Tests for the service's fused daily read against an in-memory fake database.

The fake reproduces what the SQL does: members are joined to their entity and
datasource and carry the entity's own currency, the daily read returns at most one
observation per UTC day inside the requested window, and only requested days come
back. Conversion is faked as data (see ``conftest.FakeCurrencyConversion``), so
path resolution and arithmetic are the real ones.

What is *not* here: ``Database.query_daily_last_price``'s SQL. It needs
PostgreSQL, so per ``AGENTS.md`` it belongs in ``tests/integration/``.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from entity_group_management import (
    EntityGroupEmptyMembership,
    EntityGroupInvalidRange,
    EntityGroupNoPath,
    EntityGroupNotFound,
    EntityGroupRateUnavailable,
    EntityGroupValidationError,
)
from entity_group_management.service import EntityGroupService
from libram_types.libram_types import (
    DailyPrice,
    EntityGroupMemberDetail,
    EntityGroupRecord,
)

JAN_1 = date(2026, 1, 1)
JAN_2 = date(2026, 1, 2)
JAN_3 = date(2026, 1, 3)


def observed(day: date, hour: int = 23) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 59, tzinfo=UTC)


class FakeDatabase:
    """Stand-in for ``Database`` holding entities, groups, members, and daily prices."""

    def __init__(self) -> None:
        self.entities: dict[UUID, dict] = {}
        self.groups: dict[UUID, EntityGroupRecord] = {}
        self.members: list[tuple[UUID, UUID, str]] = []
        self.daily: dict[UUID, list[DailyPrice]] = {}
        self.daily_calls: list[tuple[UUID, datetime, datetime]] = []

    # -- fixture building ---------------------------------------------------
    def add_entity(self, code: str, datasource: str, currency_id: UUID | None = None):
        entity_id = uuid4()
        self.entities[entity_id] = {
            "id": entity_id,
            "code": code,
            "datasource": datasource,
            "currency_id": currency_id,
        }
        return entity_id

    def add_group(
        self, code: str = "btc", quote_currency_id: UUID | None = None
    ) -> UUID:
        group_id = uuid4()
        self.groups[group_id] = EntityGroupRecord(
            id=group_id,
            code=code,
            name="Bitcoin reference",
            quote_currency_id=quote_currency_id,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        return group_id

    def add_member(self, group_id: UUID, entity_id: UUID, strength: str = "strong"):
        self.members.append((group_id, entity_id, strength))

    def set_daily(self, entity_id: UUID, *prices: tuple[date, str]) -> None:
        self.daily[entity_id] = [
            DailyPrice(day=day, observed_at=observed(day), value=Decimal(value))
            for day, value in prices
        ]

    # -- the reads the service makes ---------------------------------------
    def get_entity_group_by_code(self, code: str) -> EntityGroupRecord | None:
        for record in self.groups.values():
            if record.code == code:
                return record
        return None

    def list_entity_group_members(
        self, group_id: UUID
    ) -> list[EntityGroupMemberDetail]:
        rows = [
            EntityGroupMemberDetail(
                entity_id=entity_id,
                entity_code=self.entities[entity_id]["code"],
                datasource=self.entities[entity_id]["datasource"],
                strength=strength,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                currency_id=self.entities[entity_id]["currency_id"],
            )
            for member_group_id, entity_id, strength in self.members
            if member_group_id == group_id
        ]
        return sorted(rows, key=lambda row: (row.entity_code, row.datasource))

    def query_daily_last_price(
        self, entity_id: UUID, start: datetime, end: datetime
    ) -> list[DailyPrice]:
        self.daily_calls.append((entity_id, start, end))
        return [
            price
            for price in self.daily.get(entity_id, [])
            if start <= price.observed_at < end
        ]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def db():
    return FakeDatabase()


@pytest.fixture
def service(db, fx):
    return EntityGroupService(db, fx)


@pytest.fixture
def btc_group(db, fx):
    """The plan's worked example: three strong BTC feeds, one weak WBTC feed.

    The group reports in PHP; two members are USD-denominated and convert
    directly at the USD entity's own rate series, and the third is PHP-native.
    """
    php_btc = db.add_entity("BTC", "coindesk-ohlc-json")
    usd = uuid4()
    fx.add_currency(usd, "USD", in_currency=None)
    usd_btc_kraken = db.add_entity("BTC", "kraken-ticker-rest-json", currency_id=usd)
    usd_btc_chainlink = db.add_entity("BTC", "chainlink-ethereum", currency_id=usd)
    usdc = uuid4()
    fx.add_currency(usdc, "USDC", in_currency=usd)
    wbtc = db.add_entity("WBTC", "web3-uniswap-arbitrum", currency_id=usdc)

    group_id = db.add_group("btc")
    db.add_member(group_id, php_btc)
    db.add_member(group_id, usd_btc_kraken)
    db.add_member(group_id, usd_btc_chainlink)
    db.add_member(group_id, wbtc, strength="weak")

    db.set_daily(php_btc, (JAN_1, "68420.000000"))
    db.set_daily(usd_btc_kraken, (JAN_1, "1200.000000"))
    db.set_daily(usd_btc_chainlink, (JAN_1, "1200.440000"))
    db.set_daily(wbtc, (JAN_1, "1200.500000"))
    fx.set_rates(usd, {JAN_1: Decimal(57)})
    return {
        "group_id": group_id,
        "php_btc": php_btc,
        "usd": usd,
        "usd_btc_kraken": usd_btc_kraken,
        "usd_btc_chainlink": usd_btc_chainlink,
        "usdc": usdc,
        "wbtc": wbtc,
    }


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------
def test_fuses_strong_members_into_a_daily_median(service, btc_group):
    payload = service.get_fused_prices("btc", JAN_1, JAN_2)

    assert payload["group"] == {
        "code": "btc",
        "name": "Bitcoin reference",
        "membership": "strong",
        "currency": "PHP",
    }
    assert payload["range"] == {"start": "2026-01-01", "end": "2026-01-02"}
    assert len(payload["bars"]) == 1
    # 68420.00 (PHP), 1200.00 * 57 = 68400.00, 1200.44 * 57 = 68425.08
    assert payload["bars"][0]["date"] == "2026-01-01"
    assert payload["bars"][0]["value"] == "68420.000000"


def test_each_day_reports_the_sources_that_actually_contributed(service, btc_group):
    sources = service.get_fused_prices("btc", JAN_1, JAN_2)["bars"][0]["sources"]

    assert [(source["entity_code"], source["datasource"]) for source in sources] == [
        ("BTC", "chainlink-ethereum"),
        ("BTC", "coindesk-ohlc-json"),
        ("BTC", "kraken-ticker-rest-json"),
    ]
    assert [source["value"] for source in sources] == [
        "68425.080000",
        "68420.000000",
        "68400.000000",
    ]


def test_a_converted_contribution_reports_the_arithmetic_it_underwent(
    service, btc_group
):
    sources = service.get_fused_prices("btc", JAN_1, JAN_2)["bars"][0]["sources"]
    converted = {source["datasource"]: source for source in sources}

    assert converted["kraken-ticker-rest-json"]["converted_from"] == {
        "currency": "USD",
        "value": "1200.000000",
        "rate": "57",
        "direction": "direct",
    }
    # A PHP-native member was not converted, and says so rather than reporting a
    # conversion that did nothing.
    assert converted["coindesk-ohlc-json"]["converted_from"] is None


def test_observations_are_reported_as_utc_instants(service, btc_group):
    sources = service.get_fused_prices("btc", JAN_1, JAN_2)["bars"][0]["sources"]

    assert sources[0]["observed_at"] == "2026-01-01T23:59:00Z"


def test_the_member_rollup_reports_inclusion_and_weak_exclusion(service, btc_group):
    members = service.get_fused_prices("btc", JAN_1, JAN_2)["members"]

    assert members == [
        {
            "entity_code": "BTC",
            "datasource": "chainlink-ethereum",
            "strength": "strong",
            "status": "included",
        },
        {
            "entity_code": "BTC",
            "datasource": "coindesk-ohlc-json",
            "strength": "strong",
            "status": "included",
        },
        {
            "entity_code": "BTC",
            "datasource": "kraken-ticker-rest-json",
            "strength": "strong",
            "status": "included",
        },
        {
            "entity_code": "WBTC",
            "datasource": "web3-uniswap-arbitrum",
            "strength": "weak",
            "status": "excluded",
            "reason": "weak_member_excluded_by_strong_mode",
        },
    ]


def test_strong_mode_does_not_read_the_weak_member_at_all(service, db, btc_group):
    service.get_fused_prices("btc", JAN_1, JAN_2)

    assert btc_group["wbtc"] not in [call[0] for call in db.daily_calls]


def test_membership_all_admits_the_weak_member(service, db, btc_group):
    # A weak member that can be converted, so the widening is visible in the bar
    # rather than in a failure: the no-path case is its own test.
    db.entities[btc_group["wbtc"]]["currency_id"] = btc_group["usd"]

    payload = service.get_fused_prices("btc", JAN_1, JAN_2, membership="all")

    assert payload["group"]["membership"] == "all"
    assert [member["status"] for member in payload["members"]] == ["included"] * 4
    # Four contributors now: 68400.00, 68420.00, 68425.08, 68428.50.
    assert payload["bars"][0]["value"] == "68422.540000"


# ---------------------------------------------------------------------------
# conversion, per member and per rate entity
# ---------------------------------------------------------------------------
def test_a_member_already_in_the_output_currency_is_not_converted(db, fx, service):
    usd = uuid4()
    fx.add_currency(usd, "USD", in_currency=None)
    kraken = db.add_entity("BTC", "kraken-ticker-rest-json", currency_id=usd)
    group_id = db.add_group("btc", quote_currency_id=usd)
    db.add_member(group_id, kraken)
    db.set_daily(kraken, (JAN_1, "1200.000000"))
    # A rate for the same entity exists, and must not be consulted: converting a
    # value from a currency into itself is not a conversion.
    fx.set_rates(usd, {JAN_1: Decimal(57)})

    payload = service.get_fused_prices("btc", JAN_1, JAN_2)

    assert payload["group"]["currency"] == "USD"
    assert payload["bars"][0]["value"] == "1200.000000"
    assert payload["bars"][0]["sources"][0]["converted_from"] is None
    assert fx.rate_series_calls == []


def test_an_inverse_path_divides_by_the_rate(db, fx, service):
    usd = uuid4()
    fx.add_currency(usd, "USD", in_currency=None)
    php_btc = db.add_entity("BTC", "coindesk-ohlc-json")
    group_id = db.add_group("btc", quote_currency_id=usd)
    db.add_member(group_id, php_btc)
    db.set_daily(php_btc, (JAN_1, "5700.000000"))
    fx.set_rates(usd, {JAN_1: Decimal(57)})

    source = service.get_fused_prices("btc", JAN_1, JAN_2)["bars"][0]["sources"][0]

    assert source["value"] == "100.000000"
    assert source["converted_from"] == {
        "currency": "PHP",
        "value": "5700.000000",
        "rate": "57",
        "direction": "inverse",
    }


def test_one_rate_series_is_read_per_rate_entity_for_the_whole_range(db, fx, service):
    usd = uuid4()
    fx.add_currency(usd, "USD", in_currency=None)
    first = db.add_entity("BTC", "kraken-ticker-rest-json", currency_id=usd)
    second = db.add_entity("BTC", "chainlink-ethereum", currency_id=usd)
    group_id = db.add_group("btc")
    db.add_member(group_id, first)
    db.add_member(group_id, second)
    db.set_daily(first, (JAN_1, "100"), (JAN_2, "100"))
    db.set_daily(second, (JAN_1, "200"), (JAN_2, "200"))
    fx.set_rates(usd, {JAN_1: Decimal(56), JAN_2: Decimal(57)})

    payload = service.get_fused_prices("btc", JAN_1, JAN_3)

    assert len(fx.rate_series_calls) == 1
    # Each day uses its own rate: (5600 + 11200) / 2, then (5700 + 11400) / 2.
    assert [bar["value"] for bar in payload["bars"]] == ["8400.000000", "8550.000000"]


def test_the_daily_read_covers_the_range_as_utc_midnights(service, db, fx, btc_group):
    service.get_fused_prices("btc", JAN_1, JAN_3)

    _, start, end = db.daily_calls[0]
    assert start == datetime(2026, 1, 1, tzinfo=UTC)
    assert end == datetime(2026, 1, 3, tzinfo=UTC)
    # The rate series is read over the same window, not per day.
    _, rate_start, rate_end = fx.rate_series_calls[0]
    assert (rate_start, rate_end) == (start, end)


# ---------------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------------
def test_a_member_with_data_and_no_path_fails_the_request(db, fx, service, btc_group):
    # WBTC is USDC-denominated and USDC is quoted in USD, so USDC -> PHP is two
    # hops. Under membership=all the weak member is selected, and its value
    # cannot be converted.
    with pytest.raises(EntityGroupNoPath) as caught:
        service.get_fused_prices("btc", JAN_1, JAN_2, membership="all")

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "error": "fx_no_path",
        "entity_code": "WBTC",
        "datasource": "web3-uniswap-arbitrum",
        "from": "USDC",
        "to": "PHP",
    }


def test_a_member_with_no_path_and_no_data_does_not_fail_the_request(
    db, fx, service, btc_group
):
    # Decision 8's asymmetry: an absent observation is the missing data this
    # feature smooths over, so an unconvertible currency on a day with no value is
    # not a contract violation.
    db.set_daily(btc_group["wbtc"])

    payload = service.get_fused_prices("btc", JAN_1, JAN_2, membership="all")

    assert len(payload["bars"]) == 1
    assert len(payload["bars"][0]["sources"]) == 3


def test_a_missing_rate_fails_the_request_naming_the_member_and_the_day(
    service, db, btc_group
):
    db.set_daily(btc_group["usd_btc_kraken"], (JAN_1, "1200"), (JAN_2, "1300"))

    with pytest.raises(EntityGroupRateUnavailable) as caught:
        service.get_fused_prices("btc", JAN_1, JAN_3)

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "error": "fx_rate_unavailable",
        "entity_code": "BTC",
        "datasource": "kraken-ticker-rest-json",
        "pair": "USD",
        "date": "2026-01-02",
    }


def test_a_non_positive_rate_is_reported_as_unavailable(service, fx, btc_group):
    fx.set_rates(btc_group["usd"], {JAN_1: Decimal(0)})

    with pytest.raises(EntityGroupRateUnavailable) as caught:
        service.get_fused_prices("btc", JAN_1, JAN_2)

    assert caught.value.detail["pair"] == "USD"


def test_a_group_with_only_weak_members_is_an_empty_membership(db, fx, service):
    usd = uuid4()
    fx.add_currency(usd, "USD", in_currency=None)
    wbtc = db.add_entity("WBTC", "web3-uniswap-arbitrum", currency_id=usd)
    group_id = db.add_group("wbtc", quote_currency_id=usd)
    db.add_member(group_id, wbtc, strength="weak")
    db.set_daily(wbtc, (JAN_1, "1"))

    with pytest.raises(EntityGroupEmptyMembership) as caught:
        service.get_fused_prices("wbtc", JAN_1, JAN_2)

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "error": "empty_membership",
        "group": "wbtc",
        "membership": "strong",
    }


def test_an_unknown_group_is_not_found(service):
    with pytest.raises(EntityGroupNotFound) as caught:
        service.get_fused_prices("nope", JAN_1, JAN_2)

    assert caught.value.detail == {"error": "group_not_found", "group": "nope"}


def test_an_unknown_membership_value_is_rejected_before_any_read(service, db):
    with pytest.raises(EntityGroupValidationError) as caught:
        service.get_fused_prices("btc", JAN_1, JAN_2, membership="any")

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "error": "invalid_value",
        "field": "membership",
        "value": "any",
        "reason": "must be one of ['strong', 'all']",
    }
    assert db.daily_calls == []


@pytest.mark.parametrize(
    ("start", "end", "reason"),
    [
        (JAN_2, JAN_2, "start must be before end"),
        (JAN_2, JAN_1, "start must be before end"),
        (
            date(2026, 1, 1),
            date(2027, 1, 3),
            "range must not exceed 366 days",
        ),
    ],
)
def test_a_bad_range_is_rejected_before_any_read(db, fx, service, start, end, reason):
    with pytest.raises(EntityGroupInvalidRange) as caught:
        service.get_fused_prices("btc", start, end)

    assert caught.value.status_code == 422
    assert caught.value.detail["error"] == "invalid_range"
    assert caught.value.detail["reason"] == reason
    assert caught.value.detail["start"] == start.isoformat()
    assert db.daily_calls == []


def test_a_range_of_exactly_366_days_is_allowed(service, btc_group):
    payload = service.get_fused_prices("btc", JAN_1, date(2027, 1, 2))

    assert payload["range"]["end"] == "2027-01-02"


# ---------------------------------------------------------------------------
# days and membership edges
# ---------------------------------------------------------------------------
def test_a_day_no_member_observed_emits_no_bar(service, btc_group):
    payload = service.get_fused_prices("btc", JAN_1, JAN_3)

    assert [bar["date"] for bar in payload["bars"]] == ["2026-01-01"]


def test_no_member_has_data_yields_an_empty_bar_list(service, db, btc_group):
    db.daily = {}

    assert service.get_fused_prices("btc", JAN_1, JAN_2)["bars"] == []


def test_a_member_is_skipped_on_a_day_it_did_not_observe(service, db, btc_group):
    db.set_daily(btc_group["php_btc"], (JAN_1, "68420"), (JAN_2, "69000"))
    db.set_daily(btc_group["usd_btc_kraken"], (JAN_2, "1300"))
    db.set_daily(btc_group["usd_btc_chainlink"])
    fx_rates = {JAN_1: Decimal(57), JAN_2: Decimal(57)}
    service.fx.set_rates(btc_group["usd"], fx_rates)

    bars = service.get_fused_prices("btc", JAN_1, JAN_3)["bars"]

    assert [len(bar["sources"]) for bar in bars] == [1, 2]
    # Day 2's two contributors are 69000.00 (PHP) and 1300.00 * 57 = 74100.00, so
    # the median is their mean.
    assert [bar["value"] for bar in bars] == ["68420.000000", "71550.000000"]

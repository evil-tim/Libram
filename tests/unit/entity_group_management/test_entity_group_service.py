"""Tests for entity group CRUD against an in-memory fake database.

The fake reproduces the behaviour the SQL has: codes are unique, membership is
keyed by (group, entity) so an upsert cannot duplicate, deleting a group
cascades its members, and the member listing is joined and ordered by entity code
then datasource. A fake that just recorded calls would let a broken service pass.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from entity_group_management import (
    CreateEntityGroupRequest,
    EntityGroupCodeExists,
    EntityGroupEntityNotFound,
    EntityGroupMemberNotFound,
    EntityGroupMemberRequest,
    EntityGroupNotFound,
    EntityGroupValidationError,
    UpdateEntityGroupRequest,
)
from entity_group_management.service import EntityGroupService
from libram_types.libram_types import EntityGroupMemberDetail, EntityGroupRecord

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeDatabase:
    """Stand-in for ``Database`` holding groups and members in memory."""

    def __init__(self, entities=None):
        self.entities = entities or {}
        self.groups: dict[UUID, EntityGroupRecord] = {}
        self.members: dict[tuple[UUID, UUID], str] = {}
        self.update_calls = 0

    def add_entity(self, code: str, datasource: str = "test-datasource") -> UUID:
        entity_id = uuid4()
        self.entities[entity_id] = {
            "id": entity_id,
            "code": code,
            "datasource": datasource,
        }
        return entity_id

    def get_entity_by_id_raw(self, entity_id: UUID):
        return self.entities.get(entity_id)

    def list_entity_groups(self) -> list[EntityGroupRecord]:
        return sorted(self.groups.values(), key=lambda record: record.code)

    def get_entity_group_by_code(self, code: str):
        for record in self.groups.values():
            if record.code == code:
                return record
        return None

    def create_entity_group(self, code, name, description=None, quote_currency_id=None):
        record = EntityGroupRecord(
            id=uuid4(),
            code=code,
            name=name,
            description=description,
            quote_currency_id=quote_currency_id,
            created_at=NOW,
            updated_at=NOW,
        )
        self.groups[record.id] = record
        return record

    def update_entity_group(self, group_id: UUID, **values):
        record = self.groups.get(group_id)
        if record is None:
            return None
        self.update_calls += 1
        for key, value in values.items():
            setattr(record, key, value)
        record.updated_at = NOW
        return record

    def delete_entity_group(self, group_id: UUID) -> bool:
        if group_id not in self.groups:
            return False
        del self.groups[group_id]
        for key in [key for key in self.members if key[0] == group_id]:
            del self.members[key]
        return True

    def upsert_entity_group_member(
        self, group_id: UUID, entity_id: UUID, strength: str
    ):
        self.members[(group_id, entity_id)] = strength
        return self.members[(group_id, entity_id)]

    def delete_entity_group_member(self, group_id: UUID, entity_id: UUID) -> bool:
        return self.members.pop((group_id, entity_id), None) is not None

    def list_entity_group_members(
        self, group_id: UUID
    ) -> list[EntityGroupMemberDetail]:
        rows = [
            EntityGroupMemberDetail(
                entity_id=entity_id,
                entity_code=self.entities[entity_id]["code"],
                datasource=self.entities[entity_id]["datasource"],
                strength=strength,
            )
            for (member_group_id, entity_id), strength in self.members.items()
            if member_group_id == group_id
        ]
        return sorted(rows, key=lambda row: (row.entity_code, row.datasource))


@pytest.fixture
def db():
    return FakeDatabase()


@pytest.fixture
def service(db):
    return EntityGroupService(db)


def create_body(
    code: str = "btc",
    name: str = "Bitcoin reference",
    description: str | None = None,
    quote_currency_id: UUID | None = None,
) -> CreateEntityGroupRequest:
    return CreateEntityGroupRequest(
        code=code,
        name=name,
        description=description,
        quote_currency_id=quote_currency_id,
    )


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------
def test_create_group_defaults_to_php_output(service):
    group = service.create_group(create_body())

    assert group["code"] == "btc"
    assert group["name"] == "Bitcoin reference"
    assert group["quote_currency_id"] is None
    assert group["description"] is None
    assert UUID(group["id"])


def test_create_group_accepts_an_output_currency(db, service):
    usd = db.add_entity("USD")

    group = service.create_group(create_body(quote_currency_id=usd))

    assert group["quote_currency_id"] == str(usd)


def test_create_group_strips_the_code(service):
    assert service.create_group(create_body(code="  btc  "))["code"] == "btc"


def test_create_group_rejects_a_blank_code(service):
    with pytest.raises(EntityGroupValidationError) as caught:
        service.create_group(create_body(code="   "))

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "error": "invalid_value",
        "field": "code",
        "value": "   ",
        "reason": "must not be blank",
    }


def test_create_group_rejects_a_duplicate_code(service):
    service.create_group(create_body())

    with pytest.raises(EntityGroupCodeExists) as caught:
        service.create_group(create_body(name="Another"))

    assert caught.value.status_code == 409
    assert caught.value.detail == {"error": "group_code_exists", "group": "btc"}


def test_create_group_rejects_an_unknown_output_currency(service):
    unknown = uuid4()

    with pytest.raises(EntityGroupEntityNotFound) as caught:
        service.create_group(create_body(quote_currency_id=unknown))

    assert caught.value.status_code == 404
    assert caught.value.detail == {
        "error": "entity_not_found",
        "entity_id": str(unknown),
    }


def test_list_groups_is_ordered_by_code(service):
    service.create_group(create_body(code="wbtc", name="Wrapped"))
    service.create_group(create_body(code="btc", name="Bitcoin"))

    assert [group["code"] for group in service.list_groups()] == ["btc", "wbtc"]


def test_get_group_reports_a_missing_code(service):
    with pytest.raises(EntityGroupNotFound) as caught:
        service.get_group("nope")

    assert caught.value.status_code == 404
    assert caught.value.detail == {"error": "group_not_found", "group": "nope"}


def test_update_group_leaves_omitted_fields_alone(db, service):
    usd = db.add_entity("USD")
    service.create_group(create_body(description="kept", quote_currency_id=usd))

    updated = service.update_group("btc", UpdateEntityGroupRequest(name="Renamed"))

    assert updated["name"] == "Renamed"
    assert updated["description"] == "kept"
    assert updated["quote_currency_id"] == str(usd)


def test_update_group_can_return_a_group_to_php(db, service):
    usd = db.add_entity("USD")
    service.create_group(create_body(quote_currency_id=usd))

    updated = service.update_group(
        "btc", UpdateEntityGroupRequest(quote_currency_id=None)
    )

    assert updated["quote_currency_id"] is None


def test_update_group_can_clear_a_description(service):
    service.create_group(create_body(description="kept"))

    updated = service.update_group("btc", UpdateEntityGroupRequest(description=None))

    assert updated["description"] is None


def test_update_group_with_no_fields_writes_nothing(db, service):
    service.create_group(create_body())

    returned = service.update_group("btc", UpdateEntityGroupRequest())

    assert returned["name"] == "Bitcoin reference"
    assert db.update_calls == 0


def test_update_group_rejects_an_unknown_output_currency(service):
    service.create_group(create_body())

    with pytest.raises(EntityGroupEntityNotFound):
        service.update_group("btc", UpdateEntityGroupRequest(quote_currency_id=uuid4()))


def test_update_group_reports_a_missing_code(service):
    with pytest.raises(EntityGroupNotFound):
        service.update_group("nope", UpdateEntityGroupRequest(name="x"))


def test_delete_group_removes_its_members(db, service):
    btc = db.add_entity("BTC")
    service.create_group(create_body())
    service.upsert_member("btc", btc, EntityGroupMemberRequest(strength="strong"))

    assert service.delete_group("btc") == {"deleted": True, "code": "btc"}
    assert db.members == {}


def test_delete_group_reports_a_missing_code(service):
    with pytest.raises(EntityGroupNotFound):
        service.delete_group("nope")


# ---------------------------------------------------------------------------
# membership
# ---------------------------------------------------------------------------
def test_upsert_member_adds_and_reports_the_entity(db, service):
    btc = db.add_entity("BTC", "coindesk-ohlc-json")
    service.create_group(create_body())

    member = service.upsert_member(
        "btc", btc, EntityGroupMemberRequest(strength="strong")
    )

    assert member == {
        "entity_id": str(btc),
        "entity_code": "BTC",
        "datasource": "coindesk-ohlc-json",
        "strength": "strong",
        "created_at": None,
    }


def test_upsert_member_is_idempotent_and_restates_strength(db, service):
    btc = db.add_entity("BTC")
    service.create_group(create_body())

    service.upsert_member("btc", btc, EntityGroupMemberRequest(strength="strong"))
    again = service.upsert_member("btc", btc, EntityGroupMemberRequest(strength="weak"))

    assert again["strength"] == "weak"
    assert len(db.members) == 1
    assert service.list_members("btc") == [again]


def test_upsert_member_rejects_an_unknown_strength(db, service):
    btc = db.add_entity("BTC")
    service.create_group(create_body())

    with pytest.raises(EntityGroupValidationError) as caught:
        service.upsert_member(
            "btc", btc, EntityGroupMemberRequest(strength="strongest")
        )

    assert caught.value.detail["field"] == "strength"
    assert db.members == {}


def test_upsert_member_rejects_an_unknown_entity(service):
    service.create_group(create_body())
    unknown = uuid4()

    with pytest.raises(EntityGroupEntityNotFound) as caught:
        service.upsert_member(
            "btc", unknown, EntityGroupMemberRequest(strength="strong")
        )

    assert caught.value.detail["entity_id"] == str(unknown)


def test_upsert_member_rejects_an_unknown_group(db, service):
    btc = db.add_entity("BTC")

    with pytest.raises(EntityGroupNotFound):
        service.upsert_member("nope", btc, EntityGroupMemberRequest(strength="strong"))


def test_members_are_paired_with_their_datasource_and_ordered(db, service):
    # The same code on two datasources, plus a weaker wrapped member.
    btc_kraken = db.add_entity("BTC", "kraken-ticker-rest-json")
    btc_coindesk = db.add_entity("BTC", "coindesk-ohlc-json")
    wbtc = db.add_entity("WBTC", "web3-uniswap-arbitrum")
    service.create_group(create_body())
    service.upsert_member("btc", wbtc, EntityGroupMemberRequest(strength="weak"))
    service.upsert_member(
        "btc", btc_kraken, EntityGroupMemberRequest(strength="strong")
    )
    service.upsert_member(
        "btc", btc_coindesk, EntityGroupMemberRequest(strength="strong")
    )

    listed = service.list_members("btc")

    # Ordered by entity code, then datasource; identical codes stay distinguishable.
    assert [(m["entity_code"], m["datasource"]) for m in listed] == [
        ("BTC", "coindesk-ohlc-json"),
        ("BTC", "kraken-ticker-rest-json"),
        ("WBTC", "web3-uniswap-arbitrum"),
    ]
    assert [m["strength"] for m in listed] == ["strong", "strong", "weak"]


def test_delete_member_removes_only_that_membership(db, service):
    btc = db.add_entity("BTC")
    wbtc = db.add_entity("WBTC")
    service.create_group(create_body())
    service.upsert_member("btc", btc, EntityGroupMemberRequest(strength="strong"))
    service.upsert_member("btc", wbtc, EntityGroupMemberRequest(strength="weak"))

    deleted = service.delete_member("btc", wbtc)

    assert deleted == {"deleted": True, "code": "btc", "entity_id": str(wbtc)}
    assert [m["entity_code"] for m in service.list_members("btc")] == ["BTC"]


def test_delete_member_reports_a_membership_that_is_not_there(db, service):
    wbtc = db.add_entity("WBTC")
    service.create_group(create_body())

    with pytest.raises(EntityGroupMemberNotFound) as caught:
        service.delete_member("btc", wbtc)

    assert caught.value.status_code == 404
    assert caught.value.detail == {
        "error": "member_not_found",
        "group": "btc",
        "entity_id": str(wbtc),
    }


def test_delete_member_reports_a_missing_group(service):
    with pytest.raises(EntityGroupNotFound):
        service.delete_member("nope", uuid4())

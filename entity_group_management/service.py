"""Group and membership CRUD, plus the fused daily read for fused entity data.

Phase 1 of ``docs/fused_entity_data_plan.md`` is the CRUD surface: which entities
make up a group, how strongly each one represents it, and which currency the fused
result should be reported in. Phase 2 adds the read that reduces the members to
one daily value.

``quote_currency_id`` is validated only for existence. Whether a member can
actually be converted into that currency is decided when a fused series is
requested, because the verdict depends on the group's currency, on the member's
currency, and on which rate rows exist at that moment — all of which can change
after a write.

The fused read keeps conversion on the far side of a boundary: this module
resolves one ``FxPath`` per selected member and loads one rate series per rate
entity, then hands ``price_analysis.fusion`` a plain callable. Fusion itself never
sees a database or a currency module.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

from currency_conversion.conversion import convert as convert_value
from currency_conversion.service import CurrencyConversionService
from libram_database.db import Database
from libram_types.libram_types import (
    EntityGroupMemberDetail,
    EntityGroupRecord,
    FxPath,
)
from price_analysis.fusion import (
    MEMBERSHIP_STRONG,
    VALID_MEMBERSHIP_MODES,
    ConversionDetail,
    Converted,
    ExcludedMember,
    FusableMember,
    FusedBar,
    MemberSeries,
    fuse_daily,
    select_members,
)

from .models import (
    VALID_STRENGTHS,
    CreateEntityGroupRequest,
    EntityGroupCodeExists,
    EntityGroupEmptyMembership,
    EntityGroupEntityNotFound,
    EntityGroupInvalidRange,
    EntityGroupMemberNotFound,
    EntityGroupMemberRequest,
    EntityGroupNoPath,
    EntityGroupNotFound,
    EntityGroupRateUnavailable,
    EntityGroupValidationError,
    UpdateEntityGroupRequest,
)

#: The longest range a single fused request may cover. These endpoints are exposed
#: as MCP tools, and the cap is validated before any price or rate row is read.
MAX_FUSED_RANGE_DAYS = 366


class EntityGroupService:
    """CRUD over entity groups and their membership, and the fused daily read."""

    def __init__(self, db: Database, fx: CurrencyConversionService):
        self.db = db
        self.fx = fx

    # ------------------------------------------------------------------
    # groups
    # ------------------------------------------------------------------
    def create_group(self, body: CreateEntityGroupRequest) -> dict:
        code = body.code.strip()
        if not code:
            raise EntityGroupValidationError("code", body.code, "must not be blank")
        name = body.name.strip()
        if not name:
            raise EntityGroupValidationError("name", body.name, "must not be blank")
        if self.db.get_entity_group_by_code(code) is not None:
            raise EntityGroupCodeExists(code)
        if body.quote_currency_id is not None:
            self._require_entity(body.quote_currency_id)
        record = self.db.create_entity_group(
            code=code,
            name=name,
            description=body.description,
            quote_currency_id=body.quote_currency_id,
        )
        if record is None:
            # A concurrent create took the code between the check above and the
            # insert; the database reports it as a taken code rather than raising.
            raise EntityGroupCodeExists(code)
        return self._group_response(record)

    def list_groups(self) -> list[dict]:
        return [self._group_response(record) for record in self.db.list_entity_groups()]

    def get_group(self, code: str) -> dict:
        return self._group_response(self._require_group(code))

    def update_group(self, code: str, body: UpdateEntityGroupRequest) -> dict:
        record = self._require_group(code)
        fields = body.model_dump(exclude_unset=True)
        if "name" in fields:
            # name is NOT NULL, so an explicit null is a client error rather than
            # a way to clear the field, and a blank name is no more useful than a
            # blank code.
            if fields["name"] is None:
                raise EntityGroupValidationError("name", None, "must not be null")
            fields["name"] = fields["name"].strip()
            if not fields["name"]:
                raise EntityGroupValidationError("name", body.name, "must not be blank")
        # An explicit null means "back to PHP", which needs no entity to exist;
        # a value must name one.
        if fields.get("quote_currency_id") is not None:
            self._require_entity(fields["quote_currency_id"])
        if not fields:
            return self._group_response(record)
        updated = self.db.update_entity_group(record.id, **fields)
        if updated is None:
            raise EntityGroupNotFound(code)
        return self._group_response(updated)

    def delete_group(self, code: str) -> dict:
        record = self._require_group(code)
        deleted = self.db.delete_entity_group(record.id)
        if not deleted:
            raise EntityGroupNotFound(code)
        return {"deleted": True, "code": code}

    # ------------------------------------------------------------------
    # membership
    # ------------------------------------------------------------------
    def list_members(self, code: str) -> list[dict]:
        record = self._require_group(code)
        return [
            self._member_response(detail)
            for detail in self.db.list_entity_group_members(record.id)
        ]

    def upsert_member(
        self, code: str, entity_id: UUID, body: EntityGroupMemberRequest
    ) -> dict:
        """Add an entity to a group, or restate its strength. Idempotent."""
        if body.strength not in VALID_STRENGTHS:
            raise EntityGroupValidationError(
                "strength", body.strength, f"must be one of {list(VALID_STRENGTHS)}"
            )
        record = self._require_group(code)
        self._require_entity(entity_id)
        self.db.upsert_entity_group_member(record.id, entity_id, body.strength)
        # Re-read rather than echo the input, so the response carries the entity
        # code and datasource the membership now resolves to. This reads the whole
        # listing rather than adding a one-member query: a group has a handful of
        # members, and reusing the same read guarantees the response matches what
        # the listing will show.
        for detail in self.db.list_entity_group_members(record.id):
            if detail.entity_id == entity_id:
                return self._member_response(detail)
        raise EntityGroupMemberNotFound(code, entity_id)

    def delete_member(self, code: str, entity_id: UUID) -> dict:
        record = self._require_group(code)
        deleted = self.db.delete_entity_group_member(record.id, entity_id)
        if not deleted:
            raise EntityGroupMemberNotFound(code, entity_id)
        return {"deleted": True, "code": code, "entity_id": str(entity_id)}

    # ------------------------------------------------------------------
    # fused daily read
    # ------------------------------------------------------------------
    def get_fused_prices(
        self,
        code: str,
        start: date,
        end: date,
        membership: str = MEMBERSHIP_STRONG,
    ) -> dict:
        """Reduce a group's selected members to one median value per UTC day.

        Every selected member's daily series is fetched, converted into the
        group's output currency, and fused. A member with no observation on a day
        is absent from that day's contributors; a member that holds a value but
        cannot be converted fails the request, naming the member and the day.

        A member already quoted in the output currency is used unconverted and
        reports ``converted_from: null``, which is not the same as a conversion
        that did nothing.
        """
        if membership not in VALID_MEMBERSHIP_MODES:
            raise EntityGroupValidationError(
                "membership",
                membership,
                f"must be one of {list(VALID_MEMBERSHIP_MODES)}",
            )
        start_at, end_at = self._utc_range(start, end)
        group = self._require_group(code)

        members = [
            self._fusable_member(detail)
            for detail in self.db.list_entity_group_members(group.id)
        ]
        included, excluded = select_members(members, membership)
        if not included:
            # Nothing to fuse is a caller error, not an empty series: it means the
            # group has no strong members while strong mode was requested.
            raise EntityGroupEmptyMembership(code, membership)

        output_currency_id = _as_uuid(group.quote_currency_id)
        output_currency = self._currency_label(output_currency_id)

        # One path per selected member, resolved up front, and one rate series per
        # distinct rate entity for the whole range: a year-long request issues one
        # rate query per pair rather than one per day.
        paths: dict[UUID, FxPath | None] = {}
        currencies: dict[UUID, UUID | None] = {}
        rate_entities: list[UUID] = []
        for member in included:
            currency_id = _as_uuid(member.currency_id)
            currencies[member.entity_id] = currency_id
            if currency_id == output_currency_id:
                paths[member.entity_id] = None
                continue
            path = self.fx.resolve(currency_id, output_currency_id)
            paths[member.entity_id] = path
            if path is not None and path.rate_entity_id not in rate_entities:
                rate_entities.append(path.rate_entity_id)

        rates = {
            rate_entity_id: self.fx.rate_series(rate_entity_id, start_at, end_at)
            for rate_entity_id in rate_entities
        }

        series = [
            MemberSeries(
                member=member,
                prices=self.db.query_daily_last_price(
                    member.entity_id, start_at, end_at
                ),
            )
            for member in included
        ]

        def convert(
            member_series: MemberSeries, value: Decimal, day: date
        ) -> Converted:
            """Convert one member-day, or raise the failure the plan defines."""
            member = member_series.member
            path = paths[member.entity_id]
            if path is None:
                if currencies[member.entity_id] == output_currency_id:
                    return Converted(value=value)
                # Only reachable for a member that actually holds a value: no
                # observation is the missing data this feature smooths over, an
                # unconvertible observation is a contract violation.
                raise EntityGroupNoPath(
                    member.entity_code,
                    member.datasource,
                    self._currency_label(currencies[member.entity_id]),
                    output_currency,
                )
            rate = rates[path.rate_entity_id].get(day)
            if rate is None or rate <= 0:
                raise EntityGroupRateUnavailable(
                    member.entity_code,
                    member.datasource,
                    self._currency_label(path.rate_entity_id),
                    day,
                )
            return Converted(
                value=convert_value(value, path, rate),
                conversion=ConversionDetail(
                    currency=self._currency_label(currencies[member.entity_id]),
                    value=value,
                    rate=rate,
                    direction=path.direction,
                ),
            )

        bars = fuse_daily(series, start, end, convert)
        return {
            "group": {
                "code": group.code,
                "name": group.name,
                "membership": membership,
                "currency": output_currency,
            },
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "members": _member_rollup(included, excluded),
            "bars": [_fused_bar(bar) for bar in bars],
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _utc_range(self, start: date, end: date) -> tuple[datetime, datetime]:
        """Turn the requested day range into UTC midnights, or reject it.

        ``end`` is exclusive, so ``start == end`` is an empty range rather than a
        single day. The cap is enforced here, before any price or rate row is
        read, and it rejects rather than truncates.
        """
        if start >= end:
            raise EntityGroupInvalidRange(start, end, "start must be before end")
        if (end - start).days > MAX_FUSED_RANGE_DAYS:
            raise EntityGroupInvalidRange(
                start, end, f"range must not exceed {MAX_FUSED_RANGE_DAYS} days"
            )
        return (
            datetime.combine(start, time.min, tzinfo=UTC),
            datetime.combine(end, time.min, tzinfo=UTC),
        )

    def _currency_label(self, currency_id: UUID | None) -> str:
        """Return a currency's reporting label, degrading to the id if it is gone.

        ``currency_code`` raises for an entity that no longer exists; on an error
        path that must not become a 500.
        """
        try:
            return self.fx.currency_code(currency_id)
        except ValueError:
            return str(currency_id)

    @staticmethod
    def _fusable_member(detail: EntityGroupMemberDetail) -> FusableMember:
        return FusableMember(
            entity_id=detail.entity_id,
            entity_code=detail.entity_code,
            datasource=detail.datasource,
            strength=detail.strength,
            currency_id=_as_uuid(detail.currency_id),
        )

    def _require_group(self, code: str) -> EntityGroupRecord:
        record = self.db.get_entity_group_by_code(code)
        if record is None:
            raise EntityGroupNotFound(code)
        return record

    def _require_entity(self, entity_id: UUID) -> None:
        if self.db.get_entity_by_id_raw(entity_id) is None:
            raise EntityGroupEntityNotFound(entity_id)

    @staticmethod
    def _group_response(record: EntityGroupRecord) -> dict:
        return {
            "id": str(record.id),
            "code": record.code,
            "name": record.name,
            "description": record.description,
            "quote_currency_id": str(record.quote_currency_id)
            if record.quote_currency_id
            else None,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    @staticmethod
    def _member_response(detail: EntityGroupMemberDetail) -> dict:
        return {
            "entity_id": str(detail.entity_id),
            "entity_code": detail.entity_code,
            "datasource": detail.datasource,
            "strength": detail.strength,
            "created_at": detail.created_at.isoformat() if detail.created_at else None,
        }


# ---------------------------------------------------------------------------
# response assembly
# ---------------------------------------------------------------------------
def _as_uuid(value: object) -> UUID | None:
    """Coerce a stored currency id to ``UUID``.

    The database layer returns ``uuid.UUID`` for a uuid column, but a plain string
    would silently defeat the identity comparison against the group's output
    currency and turn a same-currency member into a spurious no-path failure, so
    the type is normalised rather than assumed.
    """
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))


def _iso_utc(moment: datetime) -> str:
    """Format an observation instant as UTC with a ``Z`` suffix.

    A naive value can only come from a fake or a hand-built record; it is read as
    UTC rather than rejected, because the column it mirrors is ``timestamptz``.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _member_rollup(
    included: Sequence[FusableMember], excluded: Sequence[ExcludedMember]
) -> list[dict]:
    """Report what membership resolution admitted, and what it did not.

    Ordered by entity code then datasource, the same order the membership listing
    uses, so the two views of a group agree.
    """
    rollup = [
        {
            "entity_code": member.entity_code,
            "datasource": member.datasource,
            "strength": member.strength,
            "status": "included",
        }
        for member in included
    ]
    rollup.extend(
        {
            "entity_code": item.member.entity_code,
            "datasource": item.member.datasource,
            "strength": item.member.strength,
            "status": "excluded",
            "reason": item.reason,
        }
        for item in excluded
    )
    return sorted(rollup, key=lambda row: (row["entity_code"], row["datasource"]))


def _fused_bar(bar: FusedBar) -> dict:
    """Serialize one fused day, with money as strings to keep ``Decimal`` precision."""
    return {
        "date": bar.day.isoformat(),
        "value": str(bar.value),
        "sources": [
            {
                "entity_code": source.entity_code,
                "datasource": source.datasource,
                "value": str(source.value),
                "observed_at": _iso_utc(source.observed_at),
                "converted_from": None
                if source.converted_from is None
                else {
                    "currency": source.converted_from.currency,
                    "value": str(source.converted_from.value),
                    "rate": str(source.converted_from.rate),
                    "direction": source.converted_from.direction,
                },
            }
            for source in bar.sources
        ],
    }

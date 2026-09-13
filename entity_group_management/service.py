"""Group and membership CRUD for fused entity data.

Phase 1 of ``docs/fused_entity_data_plan.md``. Nothing here computes a fused
value: this module owns which entities make up a group, how strongly each one
represents it, and which currency the fused result should be reported in.

``quote_currency_id`` is validated only for existence. Whether a member can
actually be converted into that currency is decided when a fused series is
requested, because the verdict depends on the group's currency, on the member's
currency, and on which rate rows exist at that moment — all of which can change
after a write.
"""

from __future__ import annotations

from uuid import UUID

from libram_database.db import Database
from libram_types.libram_types import EntityGroupMemberDetail, EntityGroupRecord

from .models import (
    VALID_STRENGTHS,
    CreateEntityGroupRequest,
    EntityGroupCodeExists,
    EntityGroupEntityNotFound,
    EntityGroupMemberNotFound,
    EntityGroupMemberRequest,
    EntityGroupNotFound,
    EntityGroupValidationError,
    UpdateEntityGroupRequest,
)


class EntityGroupService:
    """CRUD over entity groups and their membership."""

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # groups
    # ------------------------------------------------------------------
    def create_group(self, body: CreateEntityGroupRequest) -> dict:
        code = body.code.strip()
        if not code:
            raise EntityGroupValidationError("code", body.code, "must not be blank")
        if self.db.get_entity_group_by_code(code) is not None:
            raise EntityGroupCodeExists(code)
        if body.quote_currency_id is not None:
            self._require_entity(body.quote_currency_id)
        record = self.db.create_entity_group(
            code=code,
            name=body.name,
            description=body.description,
            quote_currency_id=body.quote_currency_id,
        )
        return self._group_response(record)

    def list_groups(self) -> list[dict]:
        return [self._group_response(record) for record in self.db.list_entity_groups()]

    def get_group(self, code: str) -> dict:
        return self._group_response(self._require_group(code))

    def update_group(self, code: str, body: UpdateEntityGroupRequest) -> dict:
        record = self._require_group(code)
        fields = body.model_dump(exclude_unset=True)
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
        # code and datasource the membership now resolves to.
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
    # helpers
    # ------------------------------------------------------------------
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

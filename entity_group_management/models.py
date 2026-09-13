"""Request models and domain errors for entity groups.

The errors carry the status code and response body the plan's error table
specifies, so the route maps any failure with one ``except`` clause and the
contract lives in one place instead of being restated per endpoint.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

#: Membership strength. ``strong`` is the actual reference; ``weak`` is a
#: derivative or wrapped representation of it.
VALID_STRENGTHS = ("strong", "weak")


class EntityGroupError(Exception):
    """Base class for every expected failure on this feature's endpoints."""

    status_code = 400
    error = "entity_group_error"

    def __init__(self, message: str, **fields: object) -> None:
        super().__init__(message)
        self.detail: dict[str, object] = {"error": self.error, **fields}


class EntityGroupNotFound(EntityGroupError):
    """No group has the requested code."""

    status_code = 404
    error = "group_not_found"

    def __init__(self, code: str) -> None:
        super().__init__(f"entity group not found: {code}", group=code)


class EntityGroupMemberNotFound(EntityGroupError):
    """The group exists but the entity is not one of its members."""

    status_code = 404
    error = "member_not_found"

    def __init__(self, code: str, entity_id: UUID) -> None:
        super().__init__(
            f"entity {entity_id} is not a member of group {code}",
            group=code,
            entity_id=str(entity_id),
        )


class EntityGroupEntityNotFound(EntityGroupError):
    """The referenced entity does not exist."""

    status_code = 404
    error = "entity_not_found"

    def __init__(self, entity_id: UUID) -> None:
        super().__init__(f"entity not found: {entity_id}", entity_id=str(entity_id))


class EntityGroupCodeExists(EntityGroupError):
    """A group already uses this code."""

    status_code = 409
    error = "group_code_exists"

    def __init__(self, code: str) -> None:
        super().__init__(f"entity group code already exists: {code}", group=code)


class EntityGroupValidationError(EntityGroupError):
    """A field is present but not acceptable."""

    status_code = 422
    error = "invalid_value"

    def __init__(self, field: str, value: object, reason: str) -> None:
        super().__init__(
            f"{field}: {reason}",
            field=field,
            value=value,
            reason=reason,
        )


class CreateEntityGroupRequest(BaseModel):
    code: str = Field(
        ..., description="Stable group code, unique across groups (e.g. btc)"
    )
    name: str = Field(..., description="Display name for the group")
    description: str | None = Field(None, description="Optional freeform description")
    quote_currency_id: UUID | None = Field(
        None,
        description=(
            "Output currency for fused values, as the UUID of a currency entity. "
            "Omit or send null for PHP, which has no entity of its own."
        ),
    )


class UpdateEntityGroupRequest(BaseModel):
    """Partial update: an omitted field is left unchanged, an explicit null clears it.

    ``name`` is the exception: its column is NOT NULL, so an explicit null is a
    client error the service rejects rather than a way to clear the field.
    """

    name: str | None = Field(None, description="Display name for the group")
    description: str | None = Field(
        None, description="Freeform description; null clears it"
    )
    quote_currency_id: UUID | None = Field(
        None,
        description="Output currency entity UUID; null returns the group to PHP",
    )


class EntityGroupMemberRequest(BaseModel):
    strength: str = Field(
        ...,
        description="strong: the actual reference. weak: a derivative or wrapped representation.",
    )


__all__ = [
    "VALID_STRENGTHS",
    "CreateEntityGroupRequest",
    "EntityGroupCodeExists",
    "EntityGroupEntityNotFound",
    "EntityGroupError",
    "EntityGroupMemberNotFound",
    "EntityGroupMemberRequest",
    "EntityGroupNotFound",
    "EntityGroupValidationError",
    "UpdateEntityGroupRequest",
]

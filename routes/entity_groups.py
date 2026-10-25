"""HTTP surface for entity groups and their membership.

Handlers parse, delegate, and map this feature's expected failures. The status
codes and response bodies in the plan's error table are encoded once on the
exception types, so every endpoint maps a failure with a single clause rather
than restating the table per route.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_entity_group_service
from entity_group_management import (
    CreateEntityGroupRequest,
    EntityGroupError,
    EntityGroupMemberRequest,
    UpdateEntityGroupRequest,
)
from entity_group_management.service import EntityGroupService

router = APIRouter()


@router.post(
    "/api/v1/entity-groups",
    operation_id="create_entity_group",
    description=(
        "Create an entity group: one top-level entity assembled from several source entities. "
        "quote_currency_id names the output currency for fused values; omit or send null for PHP."
    ),
)
async def create_entity_group(
    body: CreateEntityGroupRequest,
    service: EntityGroupService = Depends(get_entity_group_service),
):
    try:
        return service.create_group(body)
    except EntityGroupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get(
    "/api/v1/entity-groups",
    operation_id="list_entity_groups",
    description="List all entity groups ordered by code ascending.",
)
async def list_entity_groups(
    service: EntityGroupService = Depends(get_entity_group_service),
):
    return service.list_groups()


@router.get(
    "/api/v1/entity-groups/{group_code}",
    operation_id="get_entity_group",
    description="Fetch one entity group by its code.",
)
async def get_entity_group(
    group_code: str,
    service: EntityGroupService = Depends(get_entity_group_service),
):
    try:
        return service.get_group(group_code)
    except EntityGroupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch(
    "/api/v1/entity-groups/{group_code}",
    operation_id="update_entity_group",
    description=(
        "Update a group's name, description, or output currency. Omitted fields are left "
        "alone; an explicit null description or quote_currency_id clears it, and a null "
        "output currency means PHP."
    ),
)
async def update_entity_group(
    group_code: str,
    body: UpdateEntityGroupRequest,
    service: EntityGroupService = Depends(get_entity_group_service),
):
    try:
        return service.update_group(group_code, body)
    except EntityGroupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete(
    "/api/v1/entity-groups/{group_code}",
    operation_id="delete_entity_group",
    description="Delete an entity group and cascade-delete its membership.",
)
async def delete_entity_group(
    group_code: str,
    service: EntityGroupService = Depends(get_entity_group_service),
):
    try:
        return service.delete_group(group_code)
    except EntityGroupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get(
    "/api/v1/entity-groups/{group_code}/members",
    operation_id="list_entity_group_members",
    description=(
        "List a group's members ordered by entity code then datasource. Each member "
        "reports its strength: strong for the actual reference, weak for a derivative or "
        "wrapped representation."
    ),
)
async def list_entity_group_members(
    group_code: str,
    service: EntityGroupService = Depends(get_entity_group_service),
):
    try:
        return service.list_members(group_code)
    except EntityGroupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.put(
    "/api/v1/entity-groups/{group_code}/members/{entity_id}",
    operation_id="upsert_entity_group_member",
    description=(
        "Add an entity to a group or restate its strength. A full, idempotent upsert: "
        "writing the same membership twice leaves one row."
    ),
)
async def upsert_entity_group_member(
    group_code: str,
    entity_id: UUID,
    body: EntityGroupMemberRequest,
    service: EntityGroupService = Depends(get_entity_group_service),
):
    try:
        return service.upsert_member(group_code, entity_id, body)
    except EntityGroupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete(
    "/api/v1/entity-groups/{group_code}/members/{entity_id}",
    operation_id="delete_entity_group_member",
    description="Remove an entity from a group.",
)
async def delete_entity_group_member(
    group_code: str,
    entity_id: UUID,
    service: EntityGroupService = Depends(get_entity_group_service),
):
    try:
        return service.delete_member(group_code, entity_id)
    except EntityGroupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

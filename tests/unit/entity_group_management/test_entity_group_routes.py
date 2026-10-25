"""Tests for the route layer's error mapping.

Handlers are called directly rather than over HTTP, so this stays a unit test: no
server, no database. It pins the status code and response payload every expected
failure maps to, which is the part of the contract the service-level tests cannot
see — the plan's error table is otherwise only asserted through the exception
objects.

Note what this does *not* cover: FastAPI renders ``HTTPException.detail`` as
``{"detail": <payload>}``, and nothing here can see that envelope.
"""

import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException

import routes.entity_groups as routes
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

ENTITY_ID = uuid4()


class FailingService:
    """A service whose every method raises the error under test."""

    def __init__(self, error):
        self.error = error

    def _raise(self, *_args, **_kwargs):
        raise self.error

    create_group = _raise
    list_groups = _raise
    get_group = _raise
    update_group = _raise
    delete_group = _raise
    list_members = _raise
    upsert_member = _raise
    delete_member = _raise


class StubService:
    """A service that records its call and returns a sentinel."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.result

        return record


def raised(handler, service, **kwargs) -> HTTPException:
    """Call a handler and return the HTTPException it maps a domain error to."""
    with pytest.raises(HTTPException) as caught:
        asyncio.run(handler(service=service, **kwargs))
    return caught.value


CREATE_BODY = CreateEntityGroupRequest(code="btc", name="Bitcoin reference")


@pytest.mark.parametrize(
    ("handler", "kwargs", "error", "status", "detail"),
    [
        (
            routes.create_entity_group,
            {"body": CREATE_BODY},
            EntityGroupCodeExists("btc"),
            409,
            {"error": "group_code_exists", "group": "btc"},
        ),
        (
            routes.create_entity_group,
            {"body": CREATE_BODY},
            EntityGroupEntityNotFound(ENTITY_ID),
            404,
            {"error": "entity_not_found", "entity_id": str(ENTITY_ID)},
        ),
        (
            routes.create_entity_group,
            {"body": CREATE_BODY},
            EntityGroupValidationError("code", "", "must not be blank"),
            422,
            {
                "error": "invalid_value",
                "field": "code",
                "value": "",
                "reason": "must not be blank",
            },
        ),
        (
            routes.get_entity_group,
            {"group_code": "nope"},
            EntityGroupNotFound("nope"),
            404,
            {"error": "group_not_found", "group": "nope"},
        ),
        (
            routes.update_entity_group,
            {"group_code": "nope", "body": UpdateEntityGroupRequest(name="x")},
            EntityGroupNotFound("nope"),
            404,
            {"error": "group_not_found", "group": "nope"},
        ),
        (
            routes.delete_entity_group,
            {"group_code": "nope"},
            EntityGroupNotFound("nope"),
            404,
            {"error": "group_not_found", "group": "nope"},
        ),
        (
            routes.list_entity_group_members,
            {"group_code": "nope"},
            EntityGroupNotFound("nope"),
            404,
            {"error": "group_not_found", "group": "nope"},
        ),
        (
            routes.upsert_entity_group_member,
            {
                "group_code": "btc",
                "entity_id": ENTITY_ID,
                "body": EntityGroupMemberRequest(strength="strong"),
            },
            EntityGroupEntityNotFound(ENTITY_ID),
            404,
            {"error": "entity_not_found", "entity_id": str(ENTITY_ID)},
        ),
        (
            routes.delete_entity_group_member,
            {"group_code": "btc", "entity_id": ENTITY_ID},
            EntityGroupMemberNotFound("btc", ENTITY_ID),
            404,
            {
                "error": "member_not_found",
                "group": "btc",
                "entity_id": str(ENTITY_ID),
            },
        ),
    ],
)
def test_each_domain_error_maps_to_its_documented_status_and_body(
    handler, kwargs, error, status, detail
):
    mapped = raised(handler, FailingService(error), **kwargs)

    assert mapped.status_code == status
    assert mapped.detail == detail


def test_handlers_pass_the_service_result_through_unchanged():
    sentinel = {"code": "btc"}
    service = StubService(sentinel)

    assert asyncio.run(routes.list_entity_groups(service=service)) is sentinel
    assert (
        asyncio.run(routes.get_entity_group(group_code="btc", service=service))
        is sentinel
    )
    assert (
        asyncio.run(routes.create_entity_group(body=CREATE_BODY, service=service))
        is sentinel
    )
    assert [call[0] for call in service.calls] == [
        "list_groups",
        "get_group",
        "create_group",
    ]

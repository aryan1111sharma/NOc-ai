"""Integration tests for the ticket tools, via a real fastmcp.Client against the fake NOC ops backend."""

import json

import pytest
from fastmcp import Client

from tests.fake_noc_ops import build_test_server


def _payload(result):
    return json.loads(result.content[0].text)


@pytest.fixture
async def client():
    async with Client(build_test_server()) as c:
        yield c


async def test_all_ticket_tools_registered(client):
    tools = {t.name for t in await client.list_tools()}
    expected = {
        "ticket_create", "ticket_add_comment", "ticket_assign", "ticket_set_status",
        "ticket_add_visit", "ticket_reopen", "ticket_verify", "ticket_update", "ticket_link",
    }
    assert expected <= tools


async def test_ticket_create_minimal(client):
    data = _payload(await client.call_tool("ticket_create", {"title": "Charger offline", "category_id": 3}))
    assert data["title"] == "Charger offline"
    assert data["category_id"] == 3
    assert data["priority"] == "Medium"  # default applied
    assert "id" in data


async def test_ticket_create_full(client):
    data = _payload(await client.call_tool("ticket_create", {
        "title": "Gun 1 stuck locked",
        "category_id": 5,
        "priority": "High",
        "charger_id": "CHG-123",
        "tags": [1, 2],
    }))
    assert data["priority"] == "High"
    assert data["charger_id"] == "CHG-123"
    assert data["tags"] == [1, 2]


async def test_ticket_add_comment(client):
    data = _payload(await client.call_tool("ticket_add_comment", {"ticket_id": 42, "content": "checked, looks fine"}))
    assert data["ticket_id"] == 42
    assert data["content"] == "checked, looks fine"
    assert data["is_internal"] is False  # default applied


async def test_ticket_assign_to_user(client):
    data = _payload(await client.call_tool("ticket_assign", {"ticket_id": 42, "assigned_to": 7}))
    assert data["assigned_to"] == 7


async def test_ticket_assign_unassign_omits_param(client):
    # Omitting assigned_to should unassign, not send assigned_to=None over the wire.
    data = _payload(await client.call_tool("ticket_assign", {"ticket_id": 42}))
    assert data["assigned_to"] is None


async def test_ticket_set_status(client):
    data = _payload(await client.call_tool("ticket_set_status", {"ticket_id": 42, "status": "In Progress"}))
    assert data["status"] == "In Progress"


async def test_ticket_set_status_not_found(client):
    data = _payload(await client.call_tool("ticket_set_status", {"ticket_id": 999, "status": "Closed"}))
    assert "error" in data
    assert "404" in data["error"]


async def test_ticket_set_status_rejects_invalid_enum_before_any_backend_call(client):
    # status is a Literal[...] type hint, so FastMCP's own argument schema should reject an
    # invalid value before our tool code (and therefore the backend) ever sees it.
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        await client.call_tool("ticket_set_status", {"ticket_id": 42, "status": "NotARealStatus"})


async def test_ticket_add_visit(client):
    data = _payload(await client.call_tool("ticket_add_visit", {
        "ticket_id": 42, "findings": "loose connector", "status": "completed",
    }))
    assert data["findings"] == "loose connector"
    assert data["status"] == "completed"


async def test_ticket_reopen_requires_reason(client):
    data = _payload(await client.call_tool("ticket_reopen", {"ticket_id": 42, "reason": "issue recurred"}))
    assert data["status"] == "Reopened"
    assert data["reason"] == "issue recurred"


async def test_ticket_verify_confirmed_closes(client):
    data = _payload(await client.call_tool("ticket_verify", {"ticket_id": 42, "resolution_confirmed": True}))
    assert data["status"] == "Closed"


async def test_ticket_verify_not_confirmed_reopens(client):
    data = _payload(await client.call_tool("ticket_verify", {"ticket_id": 42, "resolution_confirmed": False}))
    assert data["status"] == "Reopened"


async def test_ticket_update_partial(client):
    data = _payload(await client.call_tool("ticket_update", {"ticket_id": 42, "priority": "Critical"}))
    assert data["priority"] == "Critical"
    assert "title" not in data  # untouched fields were never sent


async def test_ticket_update_not_found(client):
    data = _payload(await client.call_tool("ticket_update", {"ticket_id": 999, "title": "x"}))
    assert "error" in data
    assert "404" in data["error"]


async def test_ticket_link(client):
    data = _payload(await client.call_tool("ticket_link", {"ticket_id": 42, "linked_ticket_id": 43}))
    assert data["linked_ticket_id"] == 43
    assert data["link_type"] == "related"  # default applied

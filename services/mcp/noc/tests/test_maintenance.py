"""Integration tests for the maintenance-window tool."""

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


async def test_maintenance_tool_registered(client):
    tools = {t.name for t in await client.list_tools()}
    assert "maintenance_window_create" in tools


async def test_maintenance_window_create_defaults_suppress_both(client):
    data = _payload(await client.call_tool("maintenance_window_create", {
        "name": "Planned firmware rollout",
        "start_at": "2026-08-20T02:00:00Z",
        "end_at": "2026-08-20T04:00:00Z",
    }))
    assert data["name"] == "Planned firmware rollout"
    assert data["suppress_alerts"] is True
    assert data["suppress_notifications"] is True


async def test_maintenance_window_create_can_disable_suppression(client):
    data = _payload(await client.call_tool("maintenance_window_create", {
        "name": "Observed rollout",
        "start_at": "2026-08-20T02:00:00Z",
        "end_at": "2026-08-20T04:00:00Z",
        "suppress_alerts": False,
    }))
    assert data["suppress_alerts"] is False
    assert data["suppress_notifications"] is True

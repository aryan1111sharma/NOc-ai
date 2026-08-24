"""Integration tests for the alert lifecycle tools."""

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


async def test_alert_tools_registered(client):
    tools = {t.name for t in await client.list_tools()}
    assert {"alert_acknowledge", "alert_resolve"} <= tools


async def test_alert_acknowledge(client):
    data = _payload(await client.call_tool("alert_acknowledge", {"alert_id": 5}))
    assert data == {"alert_id": 5, "acknowledged": True}


async def test_alert_resolve(client):
    data = _payload(await client.call_tool("alert_resolve", {"alert_id": 5}))
    assert data == {"alert_id": 5, "resolved": True}

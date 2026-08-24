"""Integration tests for the charger-support tool (QR token generation)."""

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


async def test_charger_support_tool_registered(client):
    tools = {t.name for t in await client.list_tools()}
    assert "charger_generate_qr_token" in tools


async def test_generate_qr_token_minimal(client):
    data = _payload(await client.call_tool("charger_generate_qr_token", {"charger_id": "CHG-1"}))
    assert data["charger_id"] == "CHG-1"
    assert data["token"] == "signed.qr.token"
    assert "gun_id" not in data  # not passed, so not sent to the backend at all


async def test_generate_qr_token_with_gun_and_ttl(client):
    data = _payload(await client.call_tool("charger_generate_qr_token", {
        "charger_id": "CHG-1", "gun_id": 2, "ttl_hours": 6,
    }))
    assert data["gun_id"] == "2"  # query params round-trip as strings through httpx
    assert data["ttl_hours"] == "6"

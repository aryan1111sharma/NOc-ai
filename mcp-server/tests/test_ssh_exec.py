"""Tests for the SSH command-execution tools (noc_mcp.ssh_exec)."""

import pytest

from noc_mcp import ssh_exec


def setup_function():
    ssh_exec._PENDING.clear()


def test_mint_and_pop_roundtrip():
    tok = ssh_exec._mint_pending({"charger_id": "c1", "command": "uptime",
                                  "username": "torizon", "password": "s3cret"})
    assert isinstance(tok, str) and len(tok) >= 16
    req = ssh_exec._pop_pending(tok, ttl=120)
    assert req["command"] == "uptime" and req["password"] == "s3cret"


def test_pop_is_single_use():
    tok = ssh_exec._mint_pending({"charger_id": "c1", "command": "x",
                                  "username": "u", "password": "p"})
    assert ssh_exec._pop_pending(tok, ttl=120) is not None
    assert ssh_exec._pop_pending(tok, ttl=120) is None   # already consumed


def test_pop_rejects_unknown_token():
    assert ssh_exec._pop_pending("nope", ttl=120) is None


def test_pop_rejects_expired_token():
    tok = ssh_exec._mint_pending({"charger_id": "c1", "command": "x",
                                  "username": "u", "password": "p"})
    ssh_exec._PENDING[tok]["created_at"] -= 1000     # force staleness
    assert ssh_exec._pop_pending(tok, ttl=120) is None
    assert tok not in ssh_exec._PENDING              # expired entry is evicted


def test_strip_ansi_removes_escapes():
    assert ssh_exec._strip_ansi("\x1b[32mup\x1b[0m 3 days") == "up 3 days"


def test_parse_output_extracts_exit_code_and_body():
    raw = "up 3 days, load 0.10\r\n__QMCP_EXIT_0__\r\n"
    body, code = ssh_exec._parse_output(raw, command="uptime")
    assert body == "up 3 days, load 0.10"
    assert code == 0


def test_parse_output_nonzero_exit_and_drops_echoed_command():
    raw = "\x1b[0mls: cannot access 'x': No such file or directory\r\n__QMCP_EXIT_2__\r\n"
    body, code = ssh_exec._parse_output(raw, command="ls x")
    assert code == 2
    assert "No such file" in body


def test_parse_output_missing_sentinel_returns_none_code():
    body, code = ssh_exec._parse_output("partial output no marker\n", command="x")
    assert code is None and "partial output" in body


from tests.fake_ssh_ws import fake_ssh_server

TO = dict(connect_timeout=5.0, command_timeout=5.0, max_bytes=100_000)


@pytest.mark.asyncio
async def test_run_ssh_command_normal():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/NORMAL", "tok", "torizon", "p", "uptime", **TO)
    assert res["exit_code"] == 0
    assert res["output"] == "up 3 days, load 0.10"
    assert res["truncated"] is False


@pytest.mark.asyncio
async def test_run_ssh_command_nonzero_exit():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/FAIL", "tok", "u", "p", "false", **TO)
    assert res["exit_code"] == 2 and "boom" in res["output"]


@pytest.mark.asyncio
async def test_run_ssh_command_strips_ansi():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/ANSI", "tok", "u", "p", "uptime", **TO)
    assert "\x1b[" not in res["output"] and "up 3 days" in res["output"]


@pytest.mark.asyncio
async def test_run_ssh_command_truncates_huge_output():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/HUGE", "tok", "u", "p", "cat big", **TO)
    assert res["truncated"] is True


@pytest.mark.asyncio
async def test_run_ssh_command_error_frame():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/ERR", "tok", "u", "p", "uptime", **TO)
    assert "error" in res


@pytest.mark.asyncio
async def test_run_ssh_command_times_out():
    async with fake_ssh_server() as base:
        res = await ssh_exec._run_ssh_command(
            f"{base}/ws/ssh-terminal/SLOW", "tok", "u", "p", "sleep 99",
            connect_timeout=5.0, command_timeout=1.0, max_bytes=100_000)
    assert "error" in res and "timed out" in res["error"]


import httpx
import logging
from fastmcp import Client
from noc_mcp.auth import NocAuthClient
from noc_mcp.server import build_server


def _noc_client():
    """A NocAuthClient whose login returns a token (nothing else is called)."""
    def handler(request):
        return httpx.Response(200, json={"access_token": "noc.tok.jwt", "token_type": "bearer"})
    return NocAuthClient(base_url="http://noc.test", email="e", password="p",
                         transport=httpx.MockTransport(handler))


def _server(base):
    return build_server(
        client=_noc_client(),
        ssh_ws_url_builder=lambda cid: f"{base}/ws/ssh-terminal/{cid}",
    )


@pytest.mark.asyncio
async def test_prepare_returns_token_without_connecting():
    ssh_exec._PENDING.clear()
    async with fake_ssh_server() as base:
        async with Client(_server(base)) as c:
            res = (await c.call_tool("charger_ssh_prepare", {
                "charger_id": "NORMAL", "command": "uptime",
                "ssh_username": "torizon", "ssh_password": "s3cret"})).data
    assert res["charger_id"] == "NORMAL" and res["command"] == "uptime"
    assert isinstance(res["confirmation_token"], str)
    assert "password" not in str(res).lower() or "s3cret" not in str(res)  # secret not echoed
    assert ssh_exec._PENDING  # stored server-side


@pytest.mark.asyncio
async def test_execute_runs_command_end_to_end():
    ssh_exec._PENDING.clear()
    async with fake_ssh_server() as base:
        async with Client(_server(base)) as c:
            prep = (await c.call_tool("charger_ssh_prepare", {
                "charger_id": "NORMAL", "command": "uptime",
                "ssh_username": "torizon", "ssh_password": "s3cret"})).data
            res = (await c.call_tool("charger_ssh_execute",
                                     {"confirmation_token": prep["confirmation_token"]})).data
    assert res["exit_code"] == 0 and "up 3 days" in res["output"]
    assert "s3cret" not in str(res)                 # password never in output


@pytest.mark.asyncio
async def test_execute_rejects_bad_token_without_connecting():
    ssh_exec._PENDING.clear()
    async with fake_ssh_server() as base:
        async with Client(_server(base)) as c:
            res = (await c.call_tool("charger_ssh_execute",
                                     {"confirmation_token": "bogus"})).data
    assert "error" in res


@pytest.mark.asyncio
async def test_execute_audit_logs_command_not_password(caplog):
    ssh_exec._PENDING.clear()
    async with fake_ssh_server() as base:
        async with Client(_server(base)) as c:
            prep = (await c.call_tool("charger_ssh_prepare", {
                "charger_id": "NORMAL", "command": "uptime",
                "ssh_username": "torizon", "ssh_password": "s3cret"})).data
            with caplog.at_level(logging.INFO, logger="noc_mcp.ssh_audit"):
                await c.call_tool("charger_ssh_execute",
                                  {"confirmation_token": prep["confirmation_token"]})
    audit = " ".join(r.getMessage() for r in caplog.records)
    assert "uptime" in audit and "NORMAL" in audit
    assert "s3cret" not in audit                    # password never audited

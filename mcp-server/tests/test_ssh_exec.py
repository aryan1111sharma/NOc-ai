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

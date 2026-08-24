"""Shared httpx.MockTransport fake of the NOC backend for the ticket/alert/diagnostics/
maintenance/charger-support write tools (tickets.py, alerts.py, diagnostics.py,
maintenance.py, charger_support.py).

Separate from fake_noc.py (which only handles login + the per-charger proxy route) so each
fixture stays scoped to the family of tools that actually needs it.
"""

import json
import re

import httpx

from noc_mcp.auth import NocAuthClient
from noc_mcp.server import build_server

_LOGIN = "/api/v1/auth/login"

# next_id lets tests assert on a real, distinguishable id in the echoed-back response.
_NEXT_ID = {"n": 1000}


def _echo_created(body: dict, extra: dict | None = None) -> httpx.Response:
    _NEXT_ID["n"] += 1
    payload = {"id": _NEXT_ID["n"], **(body or {}), **(extra or {})}
    return httpx.Response(200, json=payload)


_ROUTES = [
    (re.compile(r"^/api/v1/tickets$"), "POST",
     lambda m, body, params: _echo_created(body, {"status": "Open"})),
    (re.compile(r"^/api/v1/tickets/(?P<ticket_id>\d+)/comments$"), "POST",
     lambda m, body, params: _echo_created(body, {"ticket_id": int(m["ticket_id"])})),
    (re.compile(r"^/api/v1/tickets/(?P<ticket_id>\d+)/assign$"), "POST",
     lambda m, body, params: httpx.Response(200, json={
         "ticket_id": int(m["ticket_id"]),
         "assigned_to": int(params["assigned_to"]) if params.get("assigned_to") else None,
         "assigned_team_id": int(params["assigned_team_id"]) if params.get("assigned_team_id") else None,
     })),
    (re.compile(r"^/api/v1/tickets/(?P<ticket_id>\d+)/status$"), "POST",
     lambda m, body, params: (
         httpx.Response(404, json={"detail": "ticket not found"})
         if m["ticket_id"] == "999"
         else httpx.Response(200, json={"ticket_id": int(m["ticket_id"]), **body})
     )),
    (re.compile(r"^/api/v1/tickets/(?P<ticket_id>\d+)/visits$"), "POST",
     lambda m, body, params: _echo_created(body, {"ticket_id": int(m["ticket_id"])})),
    (re.compile(r"^/api/v1/tickets/(?P<ticket_id>\d+)/reopen$"), "POST",
     lambda m, body, params: httpx.Response(200, json={"ticket_id": int(m["ticket_id"]), "status": "Reopened", **body})),
    (re.compile(r"^/api/v1/tickets/(?P<ticket_id>\d+)/verify$"), "POST",
     lambda m, body, params: httpx.Response(200, json={
         "ticket_id": int(m["ticket_id"]),
         "status": "Closed" if body.get("resolution_confirmed") else "Reopened",
     })),
    (re.compile(r"^/api/v1/tickets/(?P<ticket_id>\d+)$"), "PUT",
     lambda m, body, params: (
         httpx.Response(404, json={"detail": "ticket not found"})
         if m["ticket_id"] == "999"
         else httpx.Response(200, json={"ticket_id": int(m["ticket_id"]), **body})
     )),
    (re.compile(r"^/api/v1/tickets/(?P<ticket_id>\d+)/link$"), "POST",
     lambda m, body, params: _echo_created(body, {"ticket_id": int(m["ticket_id"])})),
    (re.compile(r"^/api/v1/monitoring/alerts/(?P<alert_id>\d+)/acknowledge$"), "POST",
     lambda m, body, params: httpx.Response(200, json={"alert_id": int(m["alert_id"]), "acknowledged": True})),
    (re.compile(r"^/api/v1/monitoring/alerts/(?P<alert_id>\d+)/resolve$"), "POST",
     lambda m, body, params: httpx.Response(200, json={"alert_id": int(m["alert_id"]), "resolved": True})),
    (re.compile(r"^/api/v1/chargers/(?P<charger_id>[\w-]+)/logs/download$"), "POST",
     lambda m, body, params: httpx.Response(200, json={
         "job_id": f"job-{m['charger_id']}", "status": "queued", "message": "log download queued",
     })),
    (re.compile(r"^/api/v1/chargers/(?P<charger_id>[\w-]+)/state-logs/export$"), "POST",
     lambda m, body, params: (
         httpx.Response(502, json={"detail": "charger offline"})
         if m["charger_id"] == "OFFLINE"
         else httpx.Response(200, json={"charger_id": m["charger_id"], "export_job": "exp-1", **params})
     )),
    (re.compile(r"^/api/v1/monitoring/maintenance-windows$"), "POST",
     lambda m, body, params: _echo_created(body)),
    (re.compile(r"^/api/v1/admin/chargers/(?P<charger_id>[\w-]+)/qr-token$"), "POST",
     lambda m, body, params: httpx.Response(200, json={"charger_id": m["charger_id"], "token": "signed.qr.token", **params})),
]


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith(_LOGIN):
        return httpx.Response(200, json={"access_token": "t.t.t", "token_type": "bearer"})

    params = dict(request.url.params)
    try:
        body = json.loads(request.content) if request.content else {}
    except Exception:
        body = {}

    for pattern, method, fn in _ROUTES:
        if request.method != method:
            continue
        m = pattern.match(path)
        if m:
            return fn(m.groupdict(), body, params)

    return httpx.Response(404, json={"detail": f"unhandled {request.method} {path}"})


def build_test_server():
    """FastMCP server wired to the fake NOC ops backend (no network)."""
    client = NocAuthClient(base_url="http://noc.test", email="e", password="p",
                           transport=httpx.MockTransport(_handler))
    return build_server(client)

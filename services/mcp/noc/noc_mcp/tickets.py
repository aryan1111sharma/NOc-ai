"""Ticket write tools — create/comment/assign/status/visit-log/reopen/verify/update/link.

Schemas below were read directly from the bundled OpenAPI spec (noc_openapi.json), not
guessed. Every backend field the API actually accepts is exposed; nothing invented.

Consistent with the rest of this server: never raise on a backend error, always return a
structured {"error": ...} dict so a partial failure doesn't blow up a longer tool-call chain.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import httpx

_TicketPriority = Literal["Low", "Medium", "High", "Critical"]
_TicketStatus = Literal[
    "Open", "Assigned", "In Progress", "Waiting for Customer",
    "Waiting for Parts", "Under Review", "Resolved", "Closed", "Reopened",
]
_LinkType = Literal["related", "duplicate"]


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


async def _call(client, method: str, path: str, *, json_body=None, params=None) -> dict:
    try:
        resp = await client.request(method, path, json=json_body, params=params)
    except httpx.HTTPError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        return {"error": f"NOC backend returned {resp.status_code}", "detail": detail}
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "note": "non-JSON response body"}


def _register_ticket_tools(mcp, client) -> None:
    @mcp.tool
    async def ticket_create(
        title: str,
        category_id: int,
        description: Optional[str] = None,
        priority: _TicketPriority = "Medium",
        charger_id: Optional[str] = None,
        connector_id: Optional[str] = None,
        location: Optional[str] = None,
        alarm_id: Optional[int] = None,
        session_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
        assigned_to: Optional[int] = None,
        assigned_team: Optional[str] = None,
        assigned_team_id: Optional[int] = None,
        reporting_to: Optional[int] = None,
        tags: Optional[list[int]] = None,
    ) -> dict:
        """Create a new NOC ticket. title must be 3-255 chars; description <= 5000 chars.
        Link it to a charger/session/alarm by passing the matching id if known — this is
        what lets the ticket show up in that charger's/session's history."""
        body = _drop_none({
            "title": title,
            "description": description,
            "category_id": category_id,
            "priority": priority,
            "charger_id": charger_id,
            "connector_id": connector_id,
            "location": location,
            "alarm_id": alarm_id,
            "session_id": session_id,
            "transaction_id": transaction_id,
            "assigned_to": assigned_to,
            "assigned_team": assigned_team,
            "assigned_team_id": assigned_team_id,
            "reporting_to": reporting_to,
            "tags": tags if tags is not None else [],
        })
        return await _call(client, "POST", "/api/v1/tickets", json_body=body)

    @mcp.tool
    async def ticket_add_comment(
        ticket_id: int,
        content: str,
        is_internal: bool = False,
        mentions: Optional[list[int]] = None,
    ) -> dict:
        """Add a comment to a ticket. content is required, 1-5000 chars. Set is_internal=True
        for a note not visible to the customer/reporter."""
        body = {"content": content, "is_internal": is_internal, "mentions": mentions or []}
        return await _call(client, "POST", f"/api/v1/tickets/{ticket_id}/comments", json_body=body)

    @mcp.tool
    async def ticket_assign(
        ticket_id: int,
        assigned_to: Optional[int] = None,
        assigned_team_id: Optional[int] = None,
        assigned_team: Optional[str] = None,
    ) -> dict:
        """Assign or reassign a ticket to a user and/or team. Omit assigned_to to unassign
        the individual. assigned_team is a legacy free-text team name; prefer
        assigned_team_id when the team's numeric id is known."""
        params = _drop_none({
            "assigned_to": assigned_to,
            "assigned_team_id": assigned_team_id,
            "assigned_team": assigned_team,
        })
        return await _call(client, "POST", f"/api/v1/tickets/{ticket_id}/assign", params=params)

    @mcp.tool
    async def ticket_set_status(
        ticket_id: int,
        status: _TicketStatus,
        reason: Optional[str] = None,
    ) -> dict:
        """Change a ticket's status. reason is optional free text (<=1000 chars) recorded
        with the change — supply one for anything other than routine progress updates."""
        body = _drop_none({"status": status, "reason": reason})
        return await _call(client, "POST", f"/api/v1/tickets/{ticket_id}/status", json_body=body)

    @mcp.tool
    async def ticket_add_visit(
        ticket_id: int,
        visit_date: Optional[str] = None,
        arrival_time: Optional[str] = None,
        departure_time: Optional[str] = None,
        findings: Optional[str] = None,
        actions_taken: Optional[str] = None,
        next_steps: Optional[str] = None,
        status: Literal["planned", "in_progress", "completed", "cancelled"] = "planned",
        visit_pin: Optional[str] = None,
    ) -> dict:
        """Log a field-service visit against a ticket (findings/actions/next steps). All
        fields except status are optional. visit_pin, if supplied, must be 4-6 digits."""
        body = _drop_none({
            "visit_date": visit_date,
            "arrival_time": arrival_time,
            "departure_time": departure_time,
            "findings": findings,
            "actions_taken": actions_taken,
            "next_steps": next_steps,
            "status": status,
            "visit_pin": visit_pin,
        })
        return await _call(client, "POST", f"/api/v1/tickets/{ticket_id}/visits", json_body=body)

    @mcp.tool
    async def ticket_reopen(ticket_id: int, reason: str) -> dict:
        """Reopen a closed ticket. reason is required (3-2000 chars) — explain why it's
        being reopened."""
        return await _call(
            client, "POST", f"/api/v1/tickets/{ticket_id}/reopen", json_body={"reason": reason}
        )

    @mcp.tool
    async def ticket_verify(
        ticket_id: int, resolution_confirmed: bool, notes: Optional[str] = None
    ) -> dict:
        """Verify a ticket's resolution. resolution_confirmed=True closes it as verified;
        False sends it back (effectively reopening it) — read the backend response to
        confirm which happened. notes is optional (<=2000 chars)."""
        body = _drop_none({"resolution_confirmed": resolution_confirmed, "notes": notes})
        return await _call(client, "POST", f"/api/v1/tickets/{ticket_id}/verify", json_body=body)

    @mcp.tool
    async def ticket_update(
        ticket_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        category_id: Optional[int] = None,
        priority: Optional[_TicketPriority] = None,
        assigned_to: Optional[int] = None,
        assigned_team: Optional[str] = None,
        assigned_team_id: Optional[int] = None,
        reporting_to: Optional[int] = None,
        tags: Optional[list[int]] = None,
    ) -> dict:
        """Partially update a ticket's fields. Only pass the fields you want to change —
        omitted fields are left as-is (this is a partial update, not a full replace)."""
        body = _drop_none({
            "title": title,
            "description": description,
            "category_id": category_id,
            "priority": priority,
            "assigned_to": assigned_to,
            "assigned_team": assigned_team,
            "assigned_team_id": assigned_team_id,
            "reporting_to": reporting_to,
            "tags": tags,
        })
        return await _call(client, "PUT", f"/api/v1/tickets/{ticket_id}", json_body=body)

    @mcp.tool
    async def ticket_link(
        ticket_id: int, linked_ticket_id: int, link_type: _LinkType = "related"
    ) -> dict:
        """Link one ticket to another (e.g. mark a duplicate)."""
        body = {"linked_ticket_id": linked_ticket_id, "link_type": link_type}
        return await _call(client, "POST", f"/api/v1/tickets/{ticket_id}/link", json_body=body)

import httpx
import pytest

from cz_jira_mcp.auth import JiraAuthClient
from cz_jira_mcp.server import (
    _adf_to_text,
    _comment,
    _drop_empty,
    _get_json,
    _issue_detail,
    _issue_summary,
)

_ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Charger reboots on load."}]},
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Firmware 2.3"}]}]},
        ]},
    ],
}


def test_adf_to_text_flattens():
    out = _adf_to_text(_ADF)
    assert "Charger reboots on load." in out
    assert "Firmware 2.3" in out


def test_adf_to_text_handles_none():
    assert _adf_to_text(None) == ""


def test_drop_empty_removes_blanks():
    assert _drop_empty({"a": 1, "b": None, "c": "", "d": [], "e": "x"}) == {"a": 1, "e": "x"}


def test_issue_summary_trims_to_flat_fields():
    issue = {
        "key": "QCH-1",
        "fields": {
            "summary": "Boot loop", "status": {"name": "Open"},
            "issuetype": {"name": "Bug"}, "priority": {"name": "High"},
            "assignee": {"displayName": "Asha"}, "updated": "2026-07-10T10:00:00.000+0000",
            "project": {"key": "QCH"},
        },
    }
    assert _issue_summary(issue) == {
        "key": "QCH-1", "summary": "Boot loop", "status": "Open", "type": "Bug",
        "priority": "High", "assignee": "Asha",
        "updated": "2026-07-10T10:00:00.000+0000", "project": "QCH",
    }


def test_issue_detail_adds_description_and_components():
    issue = {
        "key": "QCH-2",
        "fields": {
            "summary": "PM fault", "status": {"name": "In Progress"},
            "issuetype": {"name": "Bug"}, "reporter": {"displayName": "Ravi"},
            "created": "2026-07-01T09:00:00.000+0000", "labels": ["firmware"],
            "components": [{"name": "PowerModule"}], "description": _ADF,
        },
    }
    d = _issue_detail(issue)
    assert d["reporter"] == "Ravi"
    assert d["labels"] == ["firmware"]
    assert d["components"] == ["PowerModule"]
    assert "Charger reboots on load." in d["description"]


def test_comment_trims_and_flattens_body():
    c = {"author": {"displayName": "Asha"}, "created": "2026-07-02T08:00:00.000+0000",
         "body": _ADF}
    out = _comment(c)
    assert out["author"] == "Asha"
    assert "Firmware 2.3" in out["body"]


@pytest.mark.asyncio
async def test_get_json_raises_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errorMessages": ["Unauthorized"]})

    async with JiraAuthClient(base_url="https://cz.test", email="e", api_token="t",
                              transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(RuntimeError, match="auth failed"):
            await _get_json(c, "/rest/api/3/myself")


@pytest.mark.asyncio
async def test_get_json_raises_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})

    async with JiraAuthClient(base_url="https://cz.test", email="e", api_token="t",
                              transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(RuntimeError, match="404"):
            await _get_json(c, "/rest/api/3/issue/QCH-999")


def test_adf_to_text_reads_mention_and_emoji_attrs():
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "cc "},
                {"type": "mention", "attrs": {"id": "1", "text": "@Asha"}},
            ]},
        ],
    }
    out = _adf_to_text(doc)
    assert "@Asha" in out

    emoji_node = {"type": "emoji", "attrs": {"shortName": ":warning:", "text": "⚠️"}}
    assert _adf_to_text(emoji_node) == "⚠️"

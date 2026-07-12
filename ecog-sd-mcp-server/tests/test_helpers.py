import json

from ecog_sd_mcp.auth import cookies_to_header, load_cookie, save_cookie


def test_cookies_to_header_joins_name_value():
    cookies = [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]
    assert cookies_to_header(cookies) == "a=1; b=2"


def test_cookies_to_header_empty():
    assert cookies_to_header([]) == ""


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "ck.json"
    save_cookie(str(p), "x=1; y=2")
    assert load_cookie(str(p)) == "x=1; y=2"
    assert json.loads(p.read_text())["cookie"] == "x=1; y=2"


def test_load_cookie_missing_file_returns_empty(tmp_path):
    assert load_cookie(str(tmp_path / "nope.json")) == ""


from ecog_sd_mcp.server import _strip_html, _field, _request_summary, _comment

_REQ = {
    "issueKey": "SASW-2911",
    "reporter": {"displayName": "Chitransh Jain"},
    "createdDate": {"jira": "2026-07-01T09:00:00.000+0000"},
    "currentStatus": {"status": "Resolved"},
    "serviceDesk": {"projectKey": "SASW", "projectName": "Support"},
    "requestFieldValues": [
        {"fieldId": "summary", "label": "Summary", "value": "Invalid Undervoltage due to AC Meter"},
        {"fieldId": "description", "label": "Description", "value": "<p>Meter reads <b>low</b></p>"},
    ],
}


def test_strip_html():
    assert _strip_html("<p>Meter reads <b>low</b></p>") == "Meter reads low"
    assert _strip_html(None) == ""


def test_field_extracts_by_id():
    assert _field(_REQ, "summary") == "Invalid Undervoltage due to AC Meter"
    assert _field(_REQ, "missing") is None


def test_request_summary():
    assert _request_summary(_REQ) == {
        "key": "SASW-2911", "summary": "Invalid Undervoltage due to AC Meter",
        "status": "Resolved", "created": "2026-07-01T09:00:00.000+0000",
        "reporter": "Chitransh Jain", "serviceDesk": "SASW",
    }


def test_comment_strips_body():
    c = {"author": {"displayName": "Asha"}, "created": {"jira": "2026-07-02T08:00:00.000+0000"},
         "body": "<p>Replaced the meter</p>"}
    assert _comment(c) == {"author": "Asha", "created": "2026-07-02T08:00:00.000+0000",
                           "body": "Replaced the meter"}

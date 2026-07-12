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

"""Tests for the on-charger API catalog (noc_mcp.catalog)."""

from noc_mcp.catalog import _load_control_center_catalog


def test_catalog_includes_every_row_not_just_get():
    cat = _load_control_center_catalog()
    eps = cat["endpoints"]
    assert len(eps) == 239
    methods = {e["method"] for e in eps}
    assert {"GET", "POST", "PUT", "DELETE"} <= methods


def test_every_endpoint_carries_a_required_role():
    cat = _load_control_center_catalog()
    roles = {e["required_role"] for e in cat["endpoints"]}
    assert roles == {"customer", "operator", "admin"}


def test_non_get_endpoints_are_marked_not_callable():
    cat = _load_control_center_catalog()
    for e in cat["endpoints"]:
        if e["method"] == "GET":
            assert e["callable"] is True
        else:
            assert e["callable"] is False
            # the per-row caveat is hoisted to a single top-level key, not repeated per-row
            assert "not_callable_reason" not in e


def test_not_callable_reason_is_a_single_top_level_unscoped_prohibition():
    cat = _load_control_center_catalog()
    reason = cat["not_callable_reason"]
    assert reason
    # imperative, not merely descriptive
    assert "do not" in reason.lower()
    # unscoped: must not read as leaving SSH/shell open just because the proxy is covered
    assert "ssh" in reason.lower()
    assert "shell" in reason.lower()


def test_how_to_call_still_states_get_only():
    cat = _load_control_center_catalog()
    assert "GET" in cat["how_to_call"]
    assert "read-only" in cat["how_to_call"].lower()


def test_services_list_covers_all_seven_ports():
    cat = _load_control_center_catalog()
    assert {s["port"] for s in cat["services"]} == {8000, 8001, 8002, 8003, 8005, 8006, 8007}


def test_request_schema_is_never_exposed():
    """Deliberate: the model may learn an operation EXISTS, never how to shape its payload."""
    cat = _load_control_center_catalog()
    for e in cat["endpoints"]:
        assert not any("request" in k.lower() or "req_body" in k.lower() for k in e)

"""Tests for the on-charger API catalog (noc_mcp.catalog)."""

from noc_mcp.catalog import _load_control_center_catalog


def test_catalog_includes_every_row_not_just_get():
    cat = _load_control_center_catalog()
    eps = cat["read_endpoints"]
    assert len(eps) == 239
    methods = {e["method"] for e in eps}
    assert {"GET", "POST", "PUT", "DELETE"} <= methods


def test_every_endpoint_carries_a_required_role():
    cat = _load_control_center_catalog()
    roles = {e["required_role"] for e in cat["read_endpoints"]}
    assert roles == {"customer", "operator", "admin"}


def test_non_get_endpoints_are_marked_not_callable():
    cat = _load_control_center_catalog()
    for e in cat["read_endpoints"]:
        if e["method"] == "GET":
            assert e["callable"] is True
        else:
            assert e["callable"] is False
            assert "read-only" in e["not_callable_reason"].lower()


def test_how_to_call_still_states_get_only():
    cat = _load_control_center_catalog()
    assert "GET" in cat["how_to_call"]
    assert "read-only" in cat["how_to_call"].lower()


def test_services_list_covers_all_seven_ports():
    cat = _load_control_center_catalog()
    assert {s["port"] for s in cat["services"]} == {8000, 8001, 8002, 8003, 8005, 8006, 8007}

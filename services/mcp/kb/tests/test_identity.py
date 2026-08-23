import base64

import pytest

from kb_mcp.identity import Caller, IdentityError, is_contributor, parse_caller

ROLES = ("kb-contributor", "ADMIN")


def test_parses_headers_into_a_caller():
    c = parse_caller({"X-User-Id": "u1", "X-User-Email": "a@b.com", "X-User-Role": "ADMIN"})
    assert c == Caller(user_id="u1", email="a@b.com", role="ADMIN")


def test_header_names_are_case_insensitive():
    c = parse_caller({"x-user-id": "u1", "X-USER-ROLE": "ADMIN"})
    assert c.user_id == "u1" and c.role == "ADMIN"


def test_decodes_librechat_b64_prefixed_values():
    encoded = "b64:" + base64.b64encode("nöc@quench.local".encode()).decode()
    assert parse_caller({"x-user-id": "u1", "x-user-email": encoded}).email == "nöc@quench.local"


def test_undecodable_b64_value_becomes_empty_not_garbage():
    assert parse_caller({"x-user-id": "u1", "x-user-email": "b64:!!!"}).email == ""


def test_missing_user_id_is_refused():
    with pytest.raises(IdentityError, match="X-User-Id"):
        parse_caller({"x-user-role": "ADMIN"})


def test_empty_headers_are_refused():
    # get_http_headers() returns {} when there is no HTTP request at all
    # (stdio transport, or a bug). That must never read as an implicit grant.
    with pytest.raises(IdentityError):
        parse_caller({})


def test_whitespace_only_user_id_is_refused():
    with pytest.raises(IdentityError):
        parse_caller({"x-user-id": "   ", "x-user-role": "ADMIN"})


def test_contributor_roles_are_allowed():
    assert is_contributor(Caller("u1", "a@b.com", "kb-contributor"), ROLES)
    assert is_contributor(Caller("u1", "a@b.com", "ADMIN"), ROLES)


def test_role_match_is_case_insensitive():
    assert is_contributor(Caller("u1", "a@b.com", "admin"), ROLES)
    assert is_contributor(Caller("u1", "a@b.com", "KB-Contributor"), ROLES)


def test_other_roles_are_denied():
    assert not is_contributor(Caller("u1", "a@b.com", "noc-engineer"), ROLES)
    assert not is_contributor(Caller("u1", "a@b.com", "USER"), ROLES)


def test_absent_role_is_denied():
    assert not is_contributor(Caller("u1", "a@b.com", ""), ROLES)


def test_empty_allow_list_denies_everyone():
    assert not is_contributor(Caller("u1", "a@b.com", "ADMIN"), ())


def test_blank_entries_in_allow_list_grant_nothing():
    # KB_CONTRIBUTOR_ROLES="" or a stray comma must not create a role that
    # matches a caller with no role header.
    assert not is_contributor(Caller("u1", "a@b.com", ""), ("", "kb-contributor"))

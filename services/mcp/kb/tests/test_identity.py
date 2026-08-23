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


def test_a_b64_encoded_role_is_not_decoded():
    """Role names are always header-safe ASCII, so LibreChat never encodes
    one. Decoding anyway would leave a second, less obvious way to spell a
    privileged role."""
    encoded = "b64:" + base64.b64encode(b"ADMIN").decode()
    caller = parse_caller({"x-user-id": "u1", "x-user-role": encoded})
    assert caller.role == encoded
    assert not is_contributor(caller, ROLES)


def test_a_plain_role_still_works_after_the_decoding_change():
    caller = parse_caller({"x-user-id": "u1", "x-user-role": "kb-contributor"})
    assert is_contributor(caller, ROLES)


def test_a_kelvin_sign_does_not_impersonate_a_contributor_role():
    """U+212A casefolds to "k". An exhaustive codepoint sweep found it to be
    the only non-ASCII character folding into either default role."""
    assert not is_contributor(Caller("u1", "a@b.com", "Kb-contributor"), ROLES)
    assert not is_contributor(Caller("u1", "a@b.com", "ＡＤＭＩＮ"), ROLES)


def test_a_blank_allow_list_entry_alone_grants_nothing():
    assert not is_contributor(Caller("u1", "a@b.com", ""), ("",))
    assert not is_contributor(Caller("u1", "a@b.com", " "), (" ",))


def test_allow_list_entries_are_stripped_before_comparison():
    assert is_contributor(Caller("u1", "a@b.com", "ADMIN"), (" ADMIN ",))


def test_a_bare_string_allow_list_is_rejected():
    with pytest.raises(TypeError, match="sequence of role names"):
        is_contributor(Caller("u1", "a@b.com", "a"), "ADMIN")


def test_a_b64_encoded_whitespace_user_id_is_refused():
    """Without the post-decode strip this yields user_id="   ", which would
    pass the gate and scope an S3 prefix of chat-uploads/   /."""
    encoded = "b64:" + base64.b64encode(b"   ").decode()
    with pytest.raises(IdentityError):
        parse_caller({"x-user-id": encoded})


def test_non_mapping_headers_are_refused_as_an_identity_error():
    for bad in ([], "x", 7, object()):
        with pytest.raises(IdentityError):
            parse_caller(bad)

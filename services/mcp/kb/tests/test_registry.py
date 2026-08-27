"""The KB registry: parsing, the collisions it must refuse, and round-tripping.

The refusals matter more than the happy path here. Every collision this file
asserts on produces a plausible-looking system that serves the wrong documents
rather than an error, so an untested refusal is indistinguishable from a
missing one.
"""
import json

import pytest

from kb_mcp import registry as reg
from kb_mcp.registry import RegistryError


def _body(**over):
    body = {
        "name": "sales",
        "department": "dept-sales",
        "scope": "dept-sales",
        "folder": "dept-sales",
        "description": "Sales department knowledge base.",
    }
    body.update(over)
    return body


def _registry(*bodies):
    return {"version": 1, "knowledge_bases": list(bodies)}


def test_safe_name_matches_the_one_scopes_enforces():
    """registry.py duplicates the pattern to stay free of scopes.py's yaml
    import; if the two ever diverge, a name valid in one becomes an unsafe MCP
    tool name or collection name in the other."""
    from kb_mcp import scopes

    assert reg.SAFE_NAME.pattern == scopes._SAFE_NAME.pattern


def test_parses_a_minimal_record():
    rec = reg.parse_record(_body(), where="t")
    assert (rec.name, rec.department, rec.scope, rec.folder) == (
        "sales", "dept-sales", "dept-sales", "dept-sales")
    assert rec.shared_scopes == ()
    assert rec.min_score is None


def test_scope_and_folder_default_to_the_name():
    rec = reg.parse_record(
        {"name": "hr", "department": "dept-hr", "description": "HR."}, where="t")
    assert rec.scope == "hr"
    assert rec.folder == "hr"


@pytest.mark.parametrize("field", ["name", "department", "scope"])
@pytest.mark.parametrize("bad", ["has space", "dot.ted", "sl/ash", "üñî"])
def test_rejects_unsafe_identifiers(field, bad):
    with pytest.raises(RegistryError, match=field.split("_")[0]):
        reg.parse_record(_body(**{field: bad}), where="t")


def test_name_may_not_be_empty():
    with pytest.raises(RegistryError, match="name"):
        reg.parse_record(_body(name=""), where="t")


def test_empty_scope_and_folder_fall_back_to_the_default():
    """Absent and empty are the same request here - "use the default" - so an
    empty string must not be pushed through as an unsafe identifier."""
    rec = reg.parse_record(_body(scope="", folder=""), where="t")
    assert rec.scope == "sales"      # the KB name, not the department
    assert rec.folder == "sales"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "/abs", ".", ".."])
def test_rejects_folders_that_are_not_a_single_segment(bad):
    """folder is joined onto /knowledge, so a path here places documents
    outside the mount."""
    with pytest.raises(RegistryError, match="folder"):
        reg.parse_record(_body(folder=bad), where="t")


def test_folder_may_contain_spaces():
    """Unlike name/scope, folder is a real filesystem path and is deliberately
    not charset-constrained."""
    assert reg.parse_record(_body(folder="EV Core (2026)"), where="t").folder == "EV Core (2026)"


def test_description_is_required_and_bounded():
    with pytest.raises(RegistryError, match="description"):
        reg.parse_record(_body(description="   "), where="t")
    with pytest.raises(RegistryError, match="description"):
        reg.parse_record(_body(description="x" * (reg.MAX_DESCRIPTION_CHARS + 1)), where="t")


def test_shared_scopes_must_not_repeat_the_own_scope():
    with pytest.raises(RegistryError, match="own scope"):
        reg.parse_record(_body(shared_scopes=["dept-sales"]), where="t")


def test_shared_scopes_must_not_duplicate():
    with pytest.raises(RegistryError, match="more than once"):
        reg.parse_record(_body(shared_scopes=["ev-core", "ev-core"]), where="t")


@pytest.mark.parametrize("bad", [-0.1, 1.1, "0.5", True])
def test_rejects_out_of_range_min_score(bad):
    with pytest.raises(RegistryError, match="min_score"):
        reg.parse_record(_body(min_score=bad), where="t")


def test_refuses_two_kbs_sharing_a_scope():
    """The isolation-critical one: a shared scope means a shared Qdrant
    collection, i.e. one department reading another's documents."""
    with pytest.raises(RegistryError, match="scope"):
        reg.parse_registry(
            _registry(_body(),
                      _body(name="other", department="dept-noc",
                            scope="dept-sales", folder="other")),
            source="t")


def test_refuses_two_kbs_sharing_a_name():
    with pytest.raises(RegistryError, match="name"):
        reg.parse_registry(
            _registry(_body(), _body(department="dept-noc", scope="dept-noc", folder="dept-noc")),
            source="t")


def test_still_refuses_two_kbs_sharing_a_scope_across_departments():
    """Isolation is per-scope and survives the department relaxation."""
    with pytest.raises(RegistryError, match="scope"):
        reg.parse_registry(
            _registry(_body(name="a", scope="shared", folder="a"),
                      _body(name="b", department="dept-noc", scope="shared", folder="b")),
            source="t")


def test_refuses_two_kbs_sharing_a_folder():
    with pytest.raises(RegistryError, match="folder"):
        reg.parse_registry(
            _registry(_body(), _body(name="other", department="dept-noc", scope="dept-noc")),
            source="t")


def test_allows_many_kbs_in_one_department():
    """Knowledge bases are independent resources: a department is a grouping
    label, so several KBs may share one."""
    records = reg.parse_registry(
        _registry(_body(), _body(name="other", scope="other", folder="other")),
        source="t")
    assert [r.name for r in records] == ["sales", "other"]
    assert {r.department for r in records} == {"dept-sales"}
    # ...and they still land in different collections
    assert records[0].scope != records[1].scope


def test_department_is_optional():
    rec = reg.parse_record(
        {"name": "scratch", "description": "Ungrouped KB."}, where="t")
    assert rec.department == ""
    assert rec.scope == "scratch"


def test_scope_defaults_to_the_name_not_the_department():
    """The default that makes two KBs in one department independent."""
    a = reg.parse_record(_body(name="alpha", scope=None, folder=None), where="t")
    b = reg.parse_record(_body(name="beta", scope=None, folder=None), where="t")
    assert (a.scope, b.scope) == ("alpha", "beta")


def test_find_all_by_department_returns_every_match():
    records = reg.parse_registry(
        _registry(_body(), _body(name="other", scope="other", folder="other")),
        source="t")
    assert [r.name for r in reg.find_all_by_department(records, "dept-sales")] == ["sales", "other"]
    assert reg.departments_in_use(records) == ["dept-sales"]


def test_rejects_unknown_version():
    with pytest.raises(RegistryError, match="version"):
        reg.parse_registry({"version": 99, "knowledge_bases": []}, source="t")


def test_missing_file_is_an_empty_registry_not_an_error(tmp_path):
    assert reg.load_registry(tmp_path / "nope.json") == []


def test_malformed_json_is_an_error(tmp_path):
    p = tmp_path / "kb-registry.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(RegistryError, match="valid JSON"):
        reg.load_registry(p)


def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "kb-registry.json"
    records = reg.parse_registry(
        _registry(_body(shared_scopes=["ev-core"], min_score=0.4)), source="t")
    reg.save_registry(p, records)
    assert reg.load_registry(p) == records
    # and the file a human might open is readable, versioned and commented
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["version"] == reg.REGISTRY_VERSION
    assert raw["$comment"] == reg.FILE_COMMENT


def test_save_rewrites_the_documentation_it_manages(tmp_path):
    """A machine-managed file that strips its own explanation on first write is
    worse than one that never had it, so the comment is re-emitted, not kept."""
    p = tmp_path / "kb-registry.json"
    p.write_text(json.dumps({"$comment": "stale", "version": 1, "knowledge_bases": []}),
                 encoding="utf-8")
    reg.save_registry(p, reg.parse_registry(_registry(_body()), source="t"))
    assert json.loads(p.read_text(encoding="utf-8"))["$comment"] == reg.FILE_COMMENT


def test_save_leaves_no_temp_file_behind(tmp_path):
    p = tmp_path / "kb-registry.json"
    reg.save_registry(p, reg.parse_registry(_registry(_body()), source="t"))
    assert [q.name for q in tmp_path.iterdir()] == ["kb-registry.json"]


def test_to_raw_config_makes_every_kb_confidential():
    """Non-confidential would put a departmental KB in the shared collection
    behind a payload filter, where a filter defect could expose it."""
    records = reg.parse_registry(_registry(_body(shared_scopes=["ev-core"])), source="t")
    scopes, profiles = reg.to_raw_config(records)
    assert scopes == {"dept-sales": {"folder": "dept-sales", "confidential": True}}
    assert profiles["sales"]["scopes"] == ["dept-sales", "ev-core"]


def test_to_raw_config_omits_min_score_when_unset():
    records = reg.parse_registry(_registry(_body()), source="t")
    _, profiles = reg.to_raw_config(records)
    assert "min_score" not in profiles["sales"]

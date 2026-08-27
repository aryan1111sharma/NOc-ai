"""The Super Admin control plane, and the merge that makes it take effect.

Two properties are load-bearing and are asserted directly rather than inferred:

  * creating a KB derives its scope, profile, tools and isolation with no edit
    to scopes.yaml (test_created_kb_appears_in_the_merged_config), and
  * every KB is independent -- its own scope and its own vector collection --
    so a department may hold several without them sharing storage
    (test_a_department_may_hold_many_kbs_each_with_its_own_collection).
"""
import json

import pytest

from kb_mcp import admin, registry as reg
from kb_mcp.registry import RegistryError
from kb_mcp.scopes import ScopesConfigError, load_effective_scopes, merge_registry, parse_scopes

DEPARTMENTS = {
    "departments": [
        {"name": "dept-noc", "description": "NOC.", "adminRole": "noc-admin"},
        {"name": "dept-sales", "description": "Sales.", "adminRole": "sales-admin"},
        {"name": "dept-finance", "description": "Finance.", "adminRole": "finance-admin"},
    ]
}


@pytest.fixture
def depts(tmp_path):
    p = tmp_path / "departments.json"
    p.write_text(json.dumps(DEPARTMENTS), encoding="utf-8")
    return p


@pytest.fixture
def registry_file(tmp_path):
    p = tmp_path / "kb-registry.json"
    reg.save_registry(p, reg.parse_registry(
        {"version": 1, "knowledge_bases": [{
            "name": "noc", "department": "dept-noc", "scope": "dept-noc",
            "folder": "dept-noc", "description": "NOC KB.", "shared_scopes": ["ev-core"],
        }]}, source="seed"))
    return p


@pytest.fixture
def scopes_file(tmp_path):
    p = tmp_path / "scopes.yaml"
    p.write_text("scopes:\n  ev-core:\n    folder: ev-core\n", encoding="utf-8")
    return p


def _create(registry_file, depts, tmp_path, **over):
    kw = dict(name="sales", department="dept-sales", description="Sales KB.",
              registry_file=registry_file, departments_file=depts, knowledge_dir=tmp_path)
    kw.update(over)
    return admin.create_kb(**kw)


# --- departments come from the control plane, never from this codebase -------

def test_departments_are_read_from_the_control_plane_file(depts):
    assert [d["name"] for d in admin.departments(depts)] == [
        "dept-noc", "dept-sales", "dept-finance"]


def test_missing_departments_file_is_fatal_not_empty(tmp_path):
    """An empty list would make every create fail as 'unknown department',
    blaming the caller for a missing mount."""
    with pytest.raises(RegistryError, match="not mounted|not found"):
        admin.departments(tmp_path / "absent.json")


def test_create_accepts_a_brand_new_department_label(registry_file, depts, tmp_path):
    """No separate department-creation step: naming a new one on the KB is enough."""
    result = _create(registry_file, depts, tmp_path, name="brandnew", department="dept-nope")
    assert result["department"] == "dept-nope"
    assert result["scope"] == "brandnew"


def test_create_works_with_no_department_at_all(registry_file, depts, tmp_path):
    result = _create(registry_file, depts, tmp_path, name="ungrouped", department="")
    assert result["department"] == ""
    assert result["scope"] == "ungrouped"


def test_a_declared_department_still_reports_its_admin_role(registry_file, depts, tmp_path):
    data = json.loads(depts.read_text(encoding="utf-8"))
    data["departments"].append(
        {"name": "dept-test", "description": "Test.", "adminRole": "test-admin"})
    depts.write_text(json.dumps(data), encoding="utf-8")

    result = _create(registry_file, depts, tmp_path, name="testkb", department="dept-test")
    assert result["department"] == "dept-test"
    assert result["admin_role"] == "test-admin"
    assert result["search_tool"] == "kb_search_testkb"


# --- creating a KB -----------------------------------------------------------

def test_create_persists_the_record_and_makes_the_folder(registry_file, depts, tmp_path):
    result = _create(registry_file, depts, tmp_path)
    assert result["scope"] == "sales"          # the KB name, not the department
    assert (tmp_path / "sales").is_dir()
    names = [r.name for r in reg.load_registry(registry_file)]
    assert names == ["noc", "sales"]


def test_created_kb_appears_in_the_merged_config_without_touching_scopes_yaml(
        registry_file, depts, scopes_file, tmp_path):
    before = scopes_file.read_text(encoding="utf-8")
    _create(registry_file, depts, tmp_path)

    assert scopes_file.read_text(encoding="utf-8") == before, "scopes.yaml must not be edited"

    config = load_effective_scopes(scopes_file, registry_file)
    assert "sales" in config.profiles
    assert config.scopes["sales"].confidential is True
    assert config.profiles["sales"].scopes == ("sales",)


def test_created_kb_gets_its_own_collection(registry_file, depts, scopes_file, tmp_path):
    """Isolation is structural: a confidential scope never shares a collection."""
    from kb_mcp.store import collection_for

    _create(registry_file, depts, tmp_path)
    config = load_effective_scopes(scopes_file, registry_file)
    sales = collection_for(config.scopes["sales"], "ev_kb")
    noc = collection_for(config.scopes["dept-noc"], "ev_kb")
    assert sales == "kb_sales"
    assert sales != noc != "ev_kb"


def test_shared_scopes_are_readable_but_never_the_write_target(
        registry_file, depts, scopes_file, tmp_path):
    from kb_mcp.manage import writable_scope

    _create(registry_file, depts, tmp_path, shared_scopes=["ev-core"])
    config = load_effective_scopes(scopes_file, registry_file)
    profile = config.profiles["sales"]
    assert set(profile.scopes) == {"sales", "ev-core"}
    assert writable_scope(profile, config).name == "sales"


def test_create_refuses_a_duplicate_name(registry_file, depts, tmp_path):
    with pytest.raises(RegistryError, match="already exists"):
        _create(registry_file, depts, tmp_path, name="noc", department="dept-sales")


def test_a_department_may_hold_many_kbs_each_with_its_own_collection(
        registry_file, depts, scopes_file, tmp_path):
    """The core of the new architecture: two KBs, one department, two collections."""
    from kb_mcp.store import collection_for

    _create(registry_file, depts, tmp_path, name="noc2", department="dept-noc")
    _create(registry_file, depts, tmp_path, name="noc3", department="dept-noc")

    records = reg.load_registry(registry_file)
    in_noc = [r.name for r in reg.find_all_by_department(records, "dept-noc")]
    assert in_noc == ["noc", "noc2", "noc3"]

    config = load_effective_scopes(scopes_file, registry_file)
    colls = {n: collection_for(config.scopes[reg.find(records, n).scope], "ev_kb")
             for n in in_noc}
    assert len(set(colls.values())) == 3, colls
    assert colls["noc"] == "kb_dept_noc"
    assert colls["noc2"] == "kb_noc2"
    assert colls["noc3"] == "kb_noc3"


def test_a_refused_create_writes_nothing(registry_file, depts, tmp_path):
    before = registry_file.read_text(encoding="utf-8")
    with pytest.raises(RegistryError):
        _create(registry_file, depts, tmp_path, name="noc")   # duplicate name
    assert registry_file.read_text(encoding="utf-8") == before


# --- assigning ---------------------------------------------------------------

def test_assign_moves_a_kb_between_departments(registry_file, depts, tmp_path):
    _create(registry_file, depts, tmp_path)
    result = admin.assign_department(
        name="sales", department="dept-finance",
        registry_file=registry_file, departments_file=depts)
    assert result["previous_department"] == "dept-sales"
    assert reg.find(reg.load_registry(registry_file), "sales").department == "dept-finance"


def test_assign_is_idempotent(registry_file, depts, tmp_path):
    _create(registry_file, depts, tmp_path)
    result = admin.assign_department(
        name="sales", department="dept-sales",
        registry_file=registry_file, departments_file=depts)
    assert result["changed"] is False


def test_assign_into_an_occupied_department_is_allowed(registry_file, depts, tmp_path):
    """Moving a KB into a department that already has one is ordinary now."""
    _create(registry_file, depts, tmp_path)
    result = admin.assign_department(
        name="sales", department="dept-noc",
        registry_file=registry_file, departments_file=depts)
    assert result["changed"] is True
    records = reg.load_registry(registry_file)
    assert sorted(r.name for r in reg.find_all_by_department(records, "dept-noc")) == ["noc", "sales"]
    # the moved KB keeps its own scope, so its documents do not merge into noc's
    assert reg.find(records, "sales").scope == "sales"


def test_assign_refuses_an_unknown_kb(registry_file, depts):
    with pytest.raises(RegistryError, match="unknown knowledge base"):
        admin.assign_department(
            name="ghost", department="dept-sales",
            registry_file=registry_file, departments_file=depts)


# --- deleting ----------------------------------------------------------------

def test_delete_removes_the_record_but_keeps_documents_by_default(
        registry_file, depts, tmp_path):
    _create(registry_file, depts, tmp_path)
    (tmp_path / "sales" / "note.md").write_text("hello", encoding="utf-8")

    result = admin.delete_kb(name="sales", registry_file=registry_file, knowledge_dir=tmp_path)

    assert result["documents_purged"] is False
    assert result["documents_kept"] == 1
    assert (tmp_path / "sales" / "note.md").is_file()
    assert reg.find(reg.load_registry(registry_file), "sales") is None


def test_delete_purges_documents_only_when_asked(registry_file, depts, tmp_path):
    _create(registry_file, depts, tmp_path)
    (tmp_path / "sales" / "note.md").write_text("hello", encoding="utf-8")

    admin.delete_kb(name="sales", registry_file=registry_file,
                    knowledge_dir=tmp_path, purge_documents=True)
    assert not (tmp_path / "sales").exists()


def test_deleted_kb_disappears_from_the_merged_config(
        registry_file, depts, scopes_file, tmp_path):
    _create(registry_file, depts, tmp_path)
    admin.delete_kb(name="sales", registry_file=registry_file, knowledge_dir=tmp_path)
    config = load_effective_scopes(scopes_file, registry_file)
    assert "sales" not in config.profiles
    assert "sales" not in config.scopes


def test_delete_refuses_an_unknown_kb(registry_file, tmp_path):
    with pytest.raises(RegistryError, match="unknown knowledge base"):
        admin.delete_kb(name="ghost", registry_file=registry_file, knowledge_dir=tmp_path)


# --- the isolation claim, stated as an artefact ------------------------------

def test_access_matrix_gives_each_kb_its_own_scope_and_tool(registry_file, depts, tmp_path):
    _create(registry_file, depts, tmp_path)
    _create(registry_file, depts, tmp_path, name="sales2", department="dept-sales")
    matrix = admin.access_matrix(registry_file=registry_file, departments_file=depts)

    by_kb = {r["knowledge_base"]: r for r in matrix["knowledge_bases"]}
    assert by_kb["sales"]["search_tool"] == "kb_search_sales"
    assert by_kb["sales2"]["search_tool"] == "kb_search_sales2"
    assert by_kb["sales"]["scope"] != by_kb["sales2"]["scope"]
    assert matrix["duplicate_scopes"] == []


def test_access_matrix_groups_many_kbs_under_one_department(registry_file, depts, tmp_path):
    _create(registry_file, depts, tmp_path)
    _create(registry_file, depts, tmp_path, name="sales2", department="dept-sales")
    matrix = admin.access_matrix(registry_file=registry_file, departments_file=depts)
    sales = next(r for r in matrix["departments"] if r["department"] == "dept-sales")
    assert sorted(sales["knowledge_bases"]) == ["sales", "sales2"]


def test_access_matrix_lists_an_undeclared_department_label(registry_file, depts, tmp_path):
    _create(registry_file, depts, tmp_path, name="adhoc", department="dept-adhoc")
    matrix = admin.access_matrix(registry_file=registry_file, departments_file=depts)
    row = next(r for r in matrix["departments"] if r["department"] == "dept-adhoc")
    assert row["declared"] is False
    assert row["knowledge_bases"] == ["adhoc"]


def test_list_kbs_reports_whether_a_department_is_declared(registry_file, depts, tmp_path):
    _create(registry_file, depts, tmp_path)
    depts.write_text(json.dumps({"departments": DEPARTMENTS["departments"][:1]}), encoding="utf-8")
    listed = admin.list_kbs(registry_file=registry_file, departments_file=depts)
    sales = next(k for k in listed["knowledge_bases"] if k["name"] == "sales")
    assert sales["department_known"] is False
    noc = next(k for k in listed["knowledge_bases"] if k["name"] == "noc")
    assert noc["department_known"] is True


def test_list_kbs_offers_declared_and_in_use_departments(registry_file, depts, tmp_path):
    _create(registry_file, depts, tmp_path, name="adhoc", department="dept-adhoc")
    listed = admin.list_kbs(registry_file=registry_file, departments_file=depts)
    labels = {d["name"] for d in listed["departments"]}
    assert "dept-finance" in labels
    assert "dept-adhoc" in labels


# --- merge semantics ---------------------------------------------------------

def test_merge_refuses_a_scope_declared_in_both_places():
    records = reg.parse_registry(
        {"version": 1, "knowledge_bases": [
            {"name": "sales", "department": "dept-sales", "description": "S."}]},
        source="t")
    raw = {"scopes": {"sales": {"folder": "x"}}, "profiles": {}}
    with pytest.raises(ScopesConfigError, match="declared in scopes.yaml and in the KB registry"):
        merge_registry(raw, records, source="t")


def test_merge_refuses_a_profile_declared_in_both_places():
    records = reg.parse_registry(
        {"version": 1, "knowledge_bases": [
            {"name": "sales", "department": "dept-sales", "description": "S."}]},
        source="t")
    raw = {"scopes": {}, "profiles": {"sales": {"scopes": ["x"], "description": "d"}}}
    with pytest.raises(ScopesConfigError, match="declared in scopes.yaml and in the KB registry"):
        merge_registry(raw, records, source="t")


def test_merged_config_passes_the_same_validation_as_a_hand_written_one(
        registry_file, scopes_file):
    """The registry inherits parse_scopes' validation rather than duplicating
    it, so an invalid registry fails the same way an invalid YAML would."""
    config = load_effective_scopes(scopes_file, registry_file)
    round_tripped = parse_scopes(
        {
            "scopes": {s.name: {"folder": s.folder, "confidential": s.confidential}
                       for s in config.scopes.values()},
            "profiles": {p.name: {"scopes": list(p.scopes), "description": p.description}
                         for p in config.profiles.values()},
        },
        source="t")
    assert set(round_tripped.profiles) == set(config.profiles)


def test_registry_only_config_needs_no_profiles_in_yaml(scopes_file, registry_file):
    """scopes.yaml ships with zero profiles now; the merged result must still
    satisfy parse_scopes' 'profiles must not be empty' rule."""
    config = load_effective_scopes(scopes_file, registry_file)
    assert config.profiles and "noc" in config.profiles

"""Super-Admin control plane: create, assign, inspect and delete knowledge bases.

These operations are deliberately *only* registry writes plus the two side
effects that make a registry record real:

    the documents folder under /knowledge   (so uploads have somewhere to land)
    the Qdrant collection                    (so search has somewhere to look)

Everything else - the scope, the profile, the kb_search_/kb_upload_ tools, the
payload-filter isolation - is derived from the record by machinery that already
exists (scopes.parse_scopes, store.collection_for, server._register). Creating a
KB therefore cannot produce a half-configured one: either the record parses and
all of it is derived, or the record is refused and none of it is.

Knowledge bases are independent resources. `department` is an optional grouping
label: a department may hold any number of KBs, and a KB may have none. Nothing
is derived from it, so an operator may introduce a new department simply by
naming it here -- no separate department-creation step, no code change.

departments.json is still read, but only to *suggest* known departments and to
report which ones carry a delegated admin role. It is not a gate.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import replace
from pathlib import Path

from . import registry as reg
from .registry import KbRecord, RegistryError

log = logging.getLogger(__name__)


def departments(departments_file) -> list[dict]:
    """Read the control plane's department list.

    A missing or malformed file is fatal rather than an empty list: silently
    reporting "no departments" would make `kb_admin_create_kb` reject every
    department for what looks like a validation reason, when the real fault is
    a missing mount.
    """
    p = Path(departments_file)
    if not p.is_file():
        raise RegistryError(
            f"departments file not found: {p} - the control plane's departments.json "
            "is not mounted into this container"
        )
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"{p} is not valid JSON: {exc}") from exc

    items = raw.get("departments") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise RegistryError(f"{p}: expected a top-level 'departments' list")

    out = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            out.append({
                "name": str(item["name"]),
                "description": str(item.get("description") or ""),
                "adminRole": str(item.get("adminRole") or ""),
            })
    return out


def _lookup_department(name: str, departments_file) -> dict:
    """Best-effort metadata for a department label.

    Deliberately advisory rather than a gate. A department is a grouping label on
    an independent resource, so requiring it to pre-exist would force a separate
    department-creation step for something that carries no authority. An unknown
    label is simply a new one; the control plane stays the source of truth for
    departments that have *groups and admin roles*, and this reports whether the
    label is one of those.
    """
    if not name:
        return {"name": "", "description": "", "adminRole": "", "known": False}
    try:
        for dept in departments(departments_file):
            if dept["name"] == name:
                return {**dept, "known": True}
    except RegistryError:
        # A missing departments.json must not block KB creation now that the
        # department is only a label.
        pass
    return {"name": name, "description": "", "adminRole": "", "known": False}


def list_departments(*, registry_file, departments_file) -> dict:
    """Every department label in play: declared in the control plane, or simply
    used on a knowledge base.

    Both are offered because a department is a label, not an authority. Returning
    only declared ones would make a label an operator already introduced vanish
    from the picker, implying it had to be registered somewhere first - which is
    exactly the separate creation step this design removes.
    """
    records = reg.load_registry(registry_file)
    try:
        declared = {d["name"]: d for d in departments(departments_file)}
    except RegistryError:
        declared = {}

    labels = sorted(set(declared) | set(reg.departments_in_use(records)))
    return {
        "departments": [
            {
                "name": label,
                "description": declared.get(label, {}).get("description", ""),
                "admin_role": declared.get(label, {}).get("adminRole", ""),
                "declared": label in declared,
                "knowledge_bases": [r.name for r in reg.find_all_by_department(records, label)],
            }
            for label in labels
        ]
    }


def list_kbs(*, registry_file, departments_file, config=None, store_registry=None) -> dict:
    """Report every KB, its department, and its live index state.

    Live state is reported per KB rather than per collection (which
    kb_ingest_status already does) because the question this answers is a
    control-plane one: is the thing I created actually backed by storage yet.
    """
    records = reg.load_registry(registry_file)

    try:
        known_departments = [d["name"] for d in departments(departments_file)]
    except RegistryError:
        known_departments = []

    out = []
    for rec in records:
        item = {
            "name": rec.name,
            "department": rec.department,
            "scope": rec.scope,
            "folder": rec.folder,
            "description": rec.description,
            "shared_scopes": list(rec.shared_scopes),
            "created_at": rec.created_at,
            "search_tool": f"kb_search_{rec.name}",
            "write_tools": [f"kb_{v}_{rec.name}" for v in ("upload", "delete", "documents", "reindex")],
        }
        if store_registry is not None:
            try:
                store = store_registry.for_scope(rec.scope)
                item["collection"] = store.collection
                exists = store.c.collection_exists(store.collection)
                item["indexed"] = exists
                item["document_count"] = len(store.known_sources()) if exists else 0
            except Exception as exc:  # pragma: no cover - live Qdrant only
                item["collection_error"] = str(exc)
        # A department label outside departments.json is legitimate now (an
        # operator may name a new one when creating a KB), so it is reported as
        # information rather than flagged as a fault.
        item["department_known"] = bool(rec.department) and rec.department in known_departments
        out.append(item)

    # Every label in play: those declared in the control plane plus any introduced
    # directly on a KB. This is what lets the UI offer existing departments while
    # still accepting a brand-new one, with no department-creation step.
    declared = {d["name"] for d in (departments(departments_file) if known_departments else [])}
    all_labels = sorted(declared | set(reg.departments_in_use(records)))

    return {
        "knowledge_bases": out,
        "departments": [
            {
                "name": label,
                "description": next(
                    (d["description"] for d in (departments(departments_file) if known_departments else [])
                     if d["name"] == label), ""),
                "admin_role": next(
                    (d["adminRole"] for d in (departments(departments_file) if known_departments else [])
                     if d["name"] == label), ""),
                "declared": label in declared,
                "knowledge_bases": [r.name for r in reg.find_all_by_department(records, label)],
            }
            for label in all_labels
        ],
    }


def create_kb(
    *,
    name: str,
    department: str,
    description: str,
    registry_file,
    departments_file,
    knowledge_dir,
    scope: str | None = None,
    folder: str | None = None,
    shared_scopes=None,
    min_score: float | None = None,
) -> dict:
    """Create a knowledge base, optionally labelled with a department.

    Ordering is chosen so a failure leaves nothing behind: validate, then
    persist the record, then create the folder. The Qdrant collection is
    created by the caller's `reload` (StoreRegistry.ensure_all), which is also
    what creates it for every KB on a cold start - so the create path and the
    startup path converge on the same code instead of the collection being
    provisioned two different ways.
    """
    records = reg.load_registry(registry_file)
    dept = _lookup_department(department, departments_file)

    candidate = reg.parse_record(
        {
            "name": name,
            "department": department or "",
            # Default to the KB's own name, never the department: that is what
            # gives two KBs in one department two separate Qdrant collections.
            "scope": scope or name,
            "folder": folder or scope or name,
            "description": description,
            "shared_scopes": list(shared_scopes or []),
            "min_score": min_score,
            "created_at": reg.utc_now(),
        },
        where="kb_admin_create_kb",
    )

    if reg.find(records, candidate.name) is not None:
        raise RegistryError(
            f"knowledge base {candidate.name!r} already exists; "
            "use kb_admin_delete_kb first, or pick another name"
        )
    # parse_registry re-checks scope/folder collisions across the whole set,
    # which is the check that actually protects isolation - a new KB reusing an
    # existing scope would share its Qdrant collection.
    updated = reg.parse_registry(
        {"version": reg.REGISTRY_VERSION,
         "knowledge_bases": [r.to_json() for r in records] + [candidate.to_json()]},
        source="kb_admin_create_kb",
    )

    reg.save_registry(registry_file, updated)
    (Path(knowledge_dir) / candidate.folder).mkdir(parents=True, exist_ok=True)
    log.info("created knowledge base %r for department %r", candidate.name, candidate.department)

    return {
        "created": candidate.name,
        "department": candidate.department,
        "admin_role": dept["adminRole"],
        "scope": candidate.scope,
        "folder": candidate.folder,
        "search_tool": f"kb_search_{candidate.name}",
    }


def assign_department(*, name: str, department: str, registry_file, departments_file) -> dict:
    """Re-assign an existing KB to a different department.

    The documents, the scope and the Qdrant collection are untouched: this
    changes *who reaches the KB*, not what is in it.
    """
    records = reg.load_registry(registry_file)
    target = reg.find(records, name)
    if target is None:
        raise RegistryError(
            f"unknown knowledge base {name!r}; known: {sorted(r.name for r in records)}"
        )
    dept = _lookup_department(department, departments_file)

    if target.department == department:
        return {"knowledge_base": name, "department": department, "changed": False}

    # No conflict check: a department may hold any number of knowledge bases, so
    # moving one into an occupied department is ordinary, not an error.
    previous = target.department
    updated = [replace(r, department=department) if r.name == name else r for r in records]
    reg.save_registry(registry_file, updated)
    log.info("reassigned knowledge base %r from %r to %r", name, previous, department)

    return {
        "knowledge_base": name,
        "department": department,
        "previous_department": previous,
        "admin_role": dept["adminRole"],
        "changed": True,
    }


def delete_kb(
    *,
    name: str,
    registry_file,
    knowledge_dir,
    store_registry=None,
    purge_documents: bool = False,
) -> dict:
    """Remove a KB. Documents survive unless `purge_documents` is explicit.

    Deleting the record already makes the KB unreachable - the tools stop being
    registered and the scope stops resolving - so the destructive half is not
    needed to satisfy the request, and defaulting to it would make a
    reversible administrative action into an irreversible data loss.

    The Qdrant collection IS dropped either way: leaving it would let a later
    KB that happens to reuse the scope name inherit the old vectors, which is a
    cross-department leak wearing the right collection name.
    """
    records = reg.load_registry(registry_file)
    target = reg.find(records, name)
    if target is None:
        raise RegistryError(
            f"unknown knowledge base {name!r}; known: {sorted(r.name for r in records)}"
        )

    remaining = [r for r in records if r.name != name]
    reg.save_registry(registry_file, remaining)

    result = {
        "deleted": name,
        "department": target.department,
        "scope": target.scope,
        "documents_purged": False,
        "collection_dropped": False,
    }

    if store_registry is not None:
        try:
            store = store_registry.for_scope(target.scope)
            if store.c.collection_exists(store.collection):
                store.c.delete_collection(store.collection)
                result["collection_dropped"] = True
            result["collection"] = store.collection
        except Exception as exc:  # pragma: no cover - live Qdrant only
            # The record is already gone, so the KB is unreachable regardless.
            # Report rather than raise: re-raising here would tell the operator
            # the delete failed when the access half of it succeeded.
            result["collection_error"] = str(exc)

    folder = Path(knowledge_dir) / target.folder
    if purge_documents:
        if folder.is_dir():
            shutil.rmtree(folder)
            result["documents_purged"] = True
    elif folder.is_dir():
        kept = [p.name for p in folder.iterdir() if p.is_file() and p.name != ".gitkeep"]
        result["documents_kept"] = len(kept)
        result["documents_folder"] = str(folder)

    log.info("deleted knowledge base %r (purge=%s)", name, purge_documents)
    return result


def access_matrix(*, registry_file, departments_file) -> dict:
    """Which knowledge base each search tool reaches, derived rather than declared.

    Now that knowledge bases are independent resources, the checkable isolation
    property is per-KB, not per-department: every KB has its own scope and
    therefore its own Qdrant collection, and each kb_search_<name> tool reaches
    exactly one of them with the scope bound server-side. Two KBs sharing a scope
    or a collection is the defect this makes visible.

    Department is reported as a grouping label only. A department listing several
    knowledge bases is normal and is NOT a finding; which of them a given agent
    may use is decided by the tools attached to that agent, not by this label.
    """
    records = reg.load_registry(registry_file)
    try:
        depts = departments(departments_file)
    except RegistryError:
        depts = []
    declared = {d["name"]: d for d in depts}

    knowledge_bases = [
        {
            "knowledge_base": r.name,
            "department": r.department or None,
            "department_declared": r.department in declared if r.department else False,
            "admin_role": declared.get(r.department, {}).get("adminRole", ""),
            "scope": r.scope,
            "search_tool": f"kb_search_{r.name}",
            "shared_scopes": list(r.shared_scopes),
        }
        for r in records
    ]

    labels = sorted(set(declared) | set(reg.departments_in_use(records)))
    groups = [
        {
            "department": label,
            "declared": label in declared,
            "admin_role": declared.get(label, {}).get("adminRole", ""),
            "knowledge_bases": [r.name for r in reg.find_all_by_department(records, label)],
        }
        for label in labels
    ]

    scopes = [r.scope for r in records]
    duplicate_scopes = sorted({s for s in scopes if scopes.count(s) > 1})

    return {
        "knowledge_bases": knowledge_bases,
        "departments": groups,
        "duplicate_scopes": duplicate_scopes,
        "invariant": (
            "each knowledge base has its own scope and its own vector collection; "
            "each kb_search_<name> tool reaches exactly one knowledge base, bound "
            "server-side. duplicate_scopes must always be empty."
        ),
    }
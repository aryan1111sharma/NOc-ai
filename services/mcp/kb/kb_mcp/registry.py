"""Persistent, data-driven knowledge-base definitions.

`scopes.yaml` declares the *shared* corpus that ships with the repo (ev-core).
Everything departmental lives here instead: one JSON record per knowledge base,
in a file that sits on the same writable `/knowledge` bind mount as the corpus
itself. That location is the whole point — a KB can be created, re-assigned or
deleted without editing YAML, rebuilding the image, or restarting the container.

A record is deliberately thin. It names the things the rest of kb_mcp already
knows how to derive behaviour from, and nothing else:

    name        -> profile name -> tools  kb_search_<name>, kb_upload_<name>, ...
    scope       -> confidential scope -> its OWN Qdrant collection (store.collection_for)
    folder      -> the directory under /knowledge holding the documents
    department  -> optional grouping label, metadata only

Knowledge bases are INDEPENDENT resources. Identity is `name`, and isolation is
carried by `scope`, which defaults to the name -- so a department may hold any
number of knowledge bases and each still gets its own Qdrant collection.

`department` is a free-text label kept for grouping and display. It is stored,
never inferred, and nothing is derived from it: this module does not know which
departments exist, does not require one, and does not validate it against any
list. An operator may therefore introduce a new department simply by naming it
on a KB, with no separate department-creation step.

`shared_scopes` exists for one real case: the `noc` profile has always searched
the cross-cutting ev-core reference corpus alongside its own department scope.
Dropping that would silently change what kb_search_noc returns, so the record
carries it rather than the merge logic special-casing a profile name.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

#: Same charset scopes.py enforces, and for the same reason: `name` becomes an
#: MCP tool-name fragment and `scope` becomes Qdrant collection-name material.
#: Duplicated as a literal rather than imported so this module stays free of
#: scopes.py (which imports yaml); the two are asserted equal in the tests.
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

REGISTRY_VERSION = 1

#: Bound on how much a caller can write into a KB description. It becomes the
#: tool docstring every model sees on every list_tools, so an unbounded value
#: is a per-request token cost, not just an aesthetic problem.
MAX_DESCRIPTION_CHARS = 500


class RegistryError(ValueError):
    """A registry record is unusable, or a mutation was refused.

    Raised for malformed/absent registry files, unsafe identifiers, and for
    mutations that would collide with an existing KB. Callers surface the
    message verbatim to the operator, so it should say what to do next.
    """


@dataclass(frozen=True)
class KbRecord:
    name: str
    department: str
    scope: str
    folder: str
    description: str
    shared_scopes: tuple[str, ...] = ()
    min_score: float | None = None
    created_at: str = ""

    def to_json(self) -> dict:
        out = {
            "name": self.name,
            "department": self.department,
            "scope": self.scope,
            "folder": self.folder,
            "description": self.description,
        }
        if self.shared_scopes:
            out["shared_scopes"] = list(self.shared_scopes)
        if self.min_score is not None:
            out["min_score"] = self.min_score
        if self.created_at:
            out["created_at"] = self.created_at
        return out


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe(value, field: str, where: str) -> str:
    text = str(value or "").strip()
    if not SAFE_NAME.match(text):
        raise RegistryError(
            f"{where}: {field} {text!r} must match {SAFE_NAME.pattern} "
            "(it becomes an MCP tool name and a Qdrant collection name)"
        )
    return text


def parse_record(body, *, where: str) -> KbRecord:
    if not isinstance(body, dict):
        raise RegistryError(f"{where}: each knowledge base must be a JSON object")

    name = _safe(body.get("name"), "name", where)

    # Department is optional metadata used for grouping and display. It is NOT an
    # identity: several knowledge bases may share one, and a KB may have none.
    # When present it is still charset-constrained so it stays safe to render and
    # to group on, but nothing is derived from it.
    raw_department = str(body.get("department") or "").strip()
    department = _safe(raw_department, "department", where) if raw_department else ""

    # `scope` defaults to the KB's own NAME, never the department. That default is
    # what makes knowledge bases independent resources: two KBs in one department
    # get two scopes and therefore two Qdrant collections. Defaulting to the
    # department would silently collapse them into one shared collection, which is
    # an isolation breach wearing a plausible name.
    scope = _safe(body.get("scope") or name, "scope", where)

    # `folder` is a real filesystem path and is NOT charset-constrained (a KB
    # may legitimately live in "EV Core (2026)"), but it must stay a single
    # relative segment: it is joined onto /knowledge, so "../" or an absolute
    # path would place documents outside the mount.
    folder = str(body.get("folder") or scope).strip()
    if not folder or folder != Path(folder).name or folder in (".", ".."):
        raise RegistryError(
            f"{where}: folder {folder!r} must be a single directory name, "
            "not a path or a relative segment"
        )

    description = str(body.get("description") or "").strip()
    if not description:
        raise RegistryError(f"{where}: description is required (it becomes the tool's docstring)")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise RegistryError(
            f"{where}: description exceeds {MAX_DESCRIPTION_CHARS} characters"
        )

    raw_shared = body.get("shared_scopes") or []
    if not isinstance(raw_shared, list):
        raise RegistryError(f"{where}: shared_scopes must be a list")
    shared: list[str] = []
    for item in raw_shared:
        s = _safe(item, "shared_scopes entry", where)
        if s == scope:
            raise RegistryError(f"{where}: shared_scopes must not repeat the KB's own scope {s!r}")
        if s in shared:
            raise RegistryError(f"{where}: shared_scopes lists {s!r} more than once")
        shared.append(s)

    min_score = body.get("min_score")
    if min_score is not None:
        if not isinstance(min_score, (int, float)) or isinstance(min_score, bool):
            raise RegistryError(f"{where}: min_score must be a number")
        if not 0.0 <= min_score <= 1.0:
            raise RegistryError(f"{where}: min_score must be between 0 and 1")

    return KbRecord(
        name=name,
        department=department,
        scope=scope,
        folder=folder,
        description=description,
        shared_scopes=tuple(shared),
        min_score=min_score,
        created_at=str(body.get("created_at") or ""),
    )


def parse_registry(raw, *, source: str) -> list[KbRecord]:
    if not isinstance(raw, dict):
        raise RegistryError(f"{source}: top level must be an object")

    version = raw.get("version", REGISTRY_VERSION)
    if version != REGISTRY_VERSION:
        raise RegistryError(
            f"{source}: unsupported registry version {version!r} "
            f"(this build understands {REGISTRY_VERSION})"
        )

    items = raw.get("knowledge_bases")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise RegistryError(f"{source}: 'knowledge_bases' must be a list")

    records: list[KbRecord] = []
    seen_names: dict[str, int] = {}
    seen_scopes: dict[str, int] = {}
    seen_folders: dict[str, int] = {}

    for i, body in enumerate(items):
        rec = parse_record(body, where=f"{source}[{i}]")
        # Every one of these collisions produces a silently wrong system rather
        # than an error later: two KBs sharing a name overwrite each other's
        # tools, sharing a scope share a Qdrant collection (an isolation
        # breach), and sharing a folder cross-index each other's documents.
        for label, key, seen in (
            ("name", rec.name, seen_names),
            ("scope", rec.scope, seen_scopes),
            ("folder", rec.folder, seen_folders),
        ):
            if key in seen:
                raise RegistryError(
                    f"{source}: knowledge bases [{seen[key]}] and [{i}] both use {label} {key!r}"
                )
            seen[key] = i
        # Deliberately NO uniqueness check on department: a department may hold
        # any number of knowledge bases. Identity lives in name/scope/folder above,
        # each of which is still unique, so isolation does not depend on department.
        records.append(rec)

    return records


def load_registry(path) -> list[KbRecord]:
    """Read the registry. A missing file is an empty registry, not an error.

    Absence is the legitimate pre-migration state (and the state of a fresh
    checkout, since the corpus is gitignored), so it degrades to "no dynamic
    KBs" and leaves whatever scopes.yaml declares intact.
    """
    p = Path(path)
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"{p} is not valid JSON: {exc}") from exc
    return parse_registry(raw, source=str(p))


#: Rewritten on every save. It is defined here, rather than left in the file, so
#: that the first kb_admin_* write does not silently strip the documentation a
#: reader depends on - the registry is the artefact someone opens to understand
#: the KB layout, and a machine-managed file that loses its own explanation is
#: worse than one that never had it.
FILE_COMMENT = [
    "Knowledge bases of the AI platform - one record per KB, and the only place they are defined.",
    "",
    "This file is DATA, not code. It lives on the /knowledge bind mount, so a Super Admin can",
    "create, re-assign or delete a knowledge base with no scopes.yaml edit, no image rebuild and",
    "no container restart: kb-mcp re-reads it on an interval and re-derives everything from it.",
    "",
    "Managed by the kb_admin_* tools on the admin plane (kb-admin-mcp-server, port 8109), which",
    "is a separate endpoint from the department-facing one so a department agent has no KB",
    "administration tool to call. Hand-edit only if that plane is unavailable.",
    "",
    "Each record drives, automatically:",
    "  name       -> search profile -> tools kb_search_<name>, kb_upload_<name>, kb_delete_<name>,",
    "                kb_documents_<name>, kb_reindex_<name>",
    "  scope      -> a confidential scope -> its OWN Qdrant collection kb_<scope>, never a shared",
    "                collection with a payload filter, so isolation cannot fail open",
    "  folder     -> the document directory under knowledge/",
    "  department -> the LibreChat group the KB is assigned to (see roles/departments.json)",
    "",
    "Knowledge bases are INDEPENDENT resources. A department may hold any number of them, and",
    "department is optional metadata - grouping and display only. Isolation is per-KB: each has",
    "its own scope and therefore its own Qdrant collection, so two KBs in one department never",
    "share storage. Which KBs an agent may use is decided by the tools attached to that agent.",
    "",
    "shared_scopes grants additional READ access to non-confidential corpora declared in",
    "scopes.yaml (currently ev-core). It never becomes a write target - writes always land in",
    "the KB's own confidential scope - so a department cannot mutate shared reference material.",
    "",
    "See docs/superpowers/specs/2026-07-19-agent-centric-knowledge-scopes-design.md",
]


def save_registry(path, records) -> None:
    """Persist atomically: a half-written registry would take every KB offline
    on the next reload, including the ones the caller was not touching."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "$comment": FILE_COMMENT,
        "version": REGISTRY_VERSION,
        "knowledge_bases": [r.to_json() for r in records],
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    # Same directory as the target: os.replace is only atomic within a
    # filesystem, and /knowledge is a bind mount that may not share one with
    # the system temp dir.
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".kb-registry-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def to_raw_config(records) -> tuple[dict, dict]:
    """Project records onto the raw scopes/profiles shape scopes.parse_scopes
    already validates, so the registry inherits that validation instead of
    growing a parallel copy of it."""
    scopes: dict[str, dict] = {}
    profiles: dict[str, dict] = {}
    for rec in records:
        # Always confidential: a departmental KB gets its own Qdrant collection
        # rather than a payload filter over a shared one, so cross-department
        # exposure cannot be caused by a filter defect.
        scopes[rec.scope] = {"folder": rec.folder, "confidential": True}
        body: dict = {
            "scopes": [rec.scope, *rec.shared_scopes],
            "description": rec.description,
        }
        if rec.min_score is not None:
            body["min_score"] = rec.min_score
        profiles[rec.name] = body
    return scopes, profiles


def find(records, name: str) -> KbRecord | None:
    for rec in records:
        if rec.name == name:
            return rec
    return None


def find_all_by_department(records, department: str) -> list[KbRecord]:
    """Every KB in a department. A list, not an Optional: a department holding
    several knowledge bases is the normal case, not an error to guard against."""
    return [rec for rec in records if rec.department == department]


def departments_in_use(records) -> list[str]:
    """Departments that already appear on a record, so the UI can offer them
    without a separate department-creation step."""
    return sorted({rec.department for rec in records if rec.department})


def with_created_at(rec: KbRecord) -> KbRecord:
    return rec if rec.created_at else replace(rec, created_at=utc_now())

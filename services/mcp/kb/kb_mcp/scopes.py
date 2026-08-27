"""Load and validate the scopes/profiles config.

Pure config handling: this module knows nothing about Qdrant, MCP, or how the
corpus is walked. That keeps its validation rules cheap to test exhaustively.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Scope and profile names are identifiers, not filenames: profile names are
# embedded verbatim into the MCP tool name (f"kb_search_{profile.name}" in
# server.py), and scope names are stored as payload filter values and — for
# confidential scopes — become collection-name material via collection_for's
# slugify. FastMCP does not validate tool names itself (it warns to stderr
# and registers whatever it's given), and an unsafe-but-unique scope name
# slugifies to a working, merely odd collection name with no error at all —
# so unsafe identifiers fail silently downstream rather than loudly here.
# Constraining both to a safe charset at config-load time turns that into a
# startup error. The `folder` field is deliberately NOT constrained by this:
# it's a real filesystem path and may contain spaces or anything the OS
# allows — a scope can declare folder: "EV Core (2026)" while its own name
# stays a safe identifier.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class ScopesConfigError(ValueError):
    """The scope configuration is unusable.

    Raised for three related cases: scopes.yaml is missing or malformed; a name
    or reference in it is invalid (unsafe identifier, undeclared scope,
    duplicate); or a resolved artefact collides (two confidential scopes
    slugifying to one collection — see store.collection_for). StoreRegistry also
    raises it for a lookup of a scope it does not hold, which parse-time
    validation should already make unreachable.
    """


@dataclass(frozen=True)
class Scope:
    name: str
    folder: str
    confidential: bool = False


@dataclass(frozen=True)
class Profile:
    name: str
    scopes: tuple[str, ...]
    description: str
    min_score: float | None = None


@dataclass(frozen=True)
class ScopesConfig:
    scopes: dict[str, Scope]
    profiles: dict[str, Profile]

    def scope_for_folder(self, folder: str) -> Scope | None:
        for scope in self.scopes.values():
            if scope.folder == folder:
                return scope
        return None


class _DupCheckLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently
    keeping the last one — a duplicate scope name is an ambiguous grant."""


def _no_duplicates(loader: _DupCheckLoader, node: yaml.MappingNode) -> dict:
    out: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in out:
            raise ScopesConfigError(f"duplicate key {key!r} in scopes config")
        out[key] = loader.construct_object(value_node, deep=True)
    return out


_DupCheckLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


def _require_mapping(value, what: str, source: str) -> dict:
    if not isinstance(value, dict):
        raise ScopesConfigError(f"{source}: '{what}' must be a mapping, got {type(value).__name__}")
    if not value:
        raise ScopesConfigError(f"{source}: '{what}' must not be empty")
    return value


def parse_scopes(raw, *, source: str) -> ScopesConfig:
    if not isinstance(raw, dict):
        raise ScopesConfigError(f"{source}: top level must be a mapping")

    raw_scopes = _require_mapping(raw.get("scopes"), "scopes", source)
    raw_profiles = _require_mapping(raw.get("profiles"), "profiles", source)

    scopes: dict[str, Scope] = {}
    folder_owners: dict[str, str] = {}
    for name, body in raw_scopes.items():
        name = str(name)
        if not _SAFE_NAME.match(name):
            raise ScopesConfigError(
                f"{source}: scope name {name!r} is not a valid identifier "
                "(it is used as a payload filter value and, for confidential "
                "scopes, as collection-name material); allowed characters "
                "are letters, digits, '_', and '-'"
            )
        body = body or {}
        if not isinstance(body, dict):
            raise ScopesConfigError(f"{source}: scope '{name}' must be a mapping")

        confidential = body.get("confidential", False)
        if not isinstance(confidential, bool):
            raise ScopesConfigError(
                f"{source}: scope '{name}' confidential must be a boolean, got {confidential!r}"
            )

        folder = str(body.get("folder", name))
        if folder in folder_owners:
            raise ScopesConfigError(
                f"{source}: scopes '{folder_owners[folder]}' and '{name}' both claim folder '{folder}'"
            )
        folder_owners[folder] = name

        scopes[name] = Scope(name=name, folder=folder, confidential=confidential)

    profiles: dict[str, Profile] = {}
    for name, body in raw_profiles.items():
        name = str(name)
        if not _SAFE_NAME.match(name):
            raise ScopesConfigError(
                f"{source}: profile name {name!r} is not a valid MCP tool-name "
                f"fragment (it becomes 'kb_search_{name}'); allowed characters "
                "are letters, digits, '_', and '-'"
            )
        if not isinstance(body, dict):
            raise ScopesConfigError(f"{source}: profile '{name}' must be a mapping")

        names = body.get("scopes") or []
        if not isinstance(names, list) or not names:
            raise ScopesConfigError(f"{source}: profile '{name}' must list at least one scope")
        names = [str(n) for n in names]
        seen_scopes: set = set()
        for scope_name in names:
            if scope_name not in scopes:
                raise ScopesConfigError(
                    f"{source}: profile '{name}' references undeclared scope '{scope_name}'"
                )
            if scope_name in seen_scopes:
                raise ScopesConfigError(
                    f"{source}: profile '{name}' lists scope '{scope_name}' more than once"
                )
            seen_scopes.add(scope_name)

        description = body.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ScopesConfigError(
                f"{source}: profile '{name}' needs a non-empty description "
                "(it becomes the tool's docstring)"
            )

        min_score = body.get("min_score")
        if min_score is not None:
            if not isinstance(min_score, (int, float)) or isinstance(min_score, bool):
                raise ScopesConfigError(f"{source}: profile '{name}' min_score must be a number")
            if not 0.0 <= float(min_score) <= 1.0:
                raise ScopesConfigError(f"{source}: profile '{name}' min_score must be between 0 and 1")
            min_score = float(min_score)

        profiles[name] = Profile(
            name=name,
            scopes=tuple(names),
            description=description.strip(),
            min_score=min_score,
        )

    return ScopesConfig(scopes=scopes, profiles=profiles)


def load_scopes(path) -> ScopesConfig:
    p = Path(path)
    if not p.is_file():
        raise ScopesConfigError(f"scopes config not found: {p}")
    try:
        raw = yaml.load(p.read_text(encoding="utf-8"), Loader=_DupCheckLoader)
    except ScopesConfigError as exc:
        # Raised inside the YAML constructor, which has no access to `source` —
        # re-raise here so every error out of this module identifies the file.
        raise ScopesConfigError(f"{p}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ScopesConfigError(f"{p} is not valid YAML: {exc}") from exc
    return parse_scopes(raw, source=str(p))


def warn_missing_folders(config: ScopesConfig, knowledge_dir) -> None:
    """A declared scope whose folder does not exist yet is legitimate (config
    written before docs arrive), so warn rather than fail."""
    root = Path(knowledge_dir)
    for scope in config.scopes.values():
        if not (root / scope.folder).is_dir():
            log.warning("scope %r declares folder %r which does not exist under %s",
                        scope.name, scope.folder, root)


def _load_yaml_raw(path) -> dict:
    """Read scopes.yaml into its raw mapping without validating it.

    The registry has to be merged in *before* validation, not after: profiles
    declared in YAML may reference scopes the registry contributes (and vice
    versa), so validating the two halves separately would reject a config that
    is correct as a whole.
    """
    p = Path(path)
    if not p.is_file():
        raise ScopesConfigError(f"scopes config not found: {p}")
    try:
        raw = yaml.load(p.read_text(encoding="utf-8"), Loader=_DupCheckLoader)
    except ScopesConfigError:
        raise
    except yaml.YAMLError as exc:
        raise ScopesConfigError(f"{p} is not valid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ScopesConfigError(f"{p}: top level must be a mapping")
    return raw


def merge_registry(raw: dict, records, *, source: str) -> dict:
    """Overlay registry-defined knowledge bases onto the raw YAML config.

    Collisions are refused rather than resolved. A silent winner here would
    mean a KB the Super Admin created is quietly not the one being served, or
    that a departmental scope resolves to the shared corpus - both look like
    working systems while pointing at the wrong documents.
    """
    from .registry import to_raw_config

    scopes = dict(raw.get("scopes") or {})
    profiles = dict(raw.get("profiles") or {})
    reg_scopes, reg_profiles = to_raw_config(records)

    for name in reg_scopes:
        if name in scopes:
            raise ScopesConfigError(
                f"{source}: scope {name!r} is declared in scopes.yaml and in the KB "
                "registry; remove it from scopes.yaml (the registry owns departmental scopes)"
            )
    for name in reg_profiles:
        if name in profiles:
            raise ScopesConfigError(
                f"{source}: profile {name!r} is declared in scopes.yaml and in the KB "
                "registry; remove it from scopes.yaml (the registry owns departmental profiles)"
            )

    scopes.update(reg_scopes)
    profiles.update(reg_profiles)
    return {"scopes": scopes, "profiles": profiles}


def load_effective_scopes(scopes_file, registry_file) -> ScopesConfig:
    """The config the server actually serves: scopes.yaml + the KB registry.

    scopes.yaml keeps the shared, repo-shipped corpus; the registry holds every
    departmental KB. Splitting them this way is what removes the "edit YAML and
    restart" step from creating a KB, without giving up any of the validation
    parse_scopes already performs on the combined result.
    """
    from .registry import load_registry

    raw = _load_yaml_raw(scopes_file)
    records = load_registry(registry_file)
    merged = merge_registry(raw, records, source=str(scopes_file))
    return parse_scopes(merged, source=f"{scopes_file}+{registry_file}")

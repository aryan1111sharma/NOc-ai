"""Load and validate the scopes/profiles config.

Pure config handling: this module knows nothing about Qdrant, MCP, or how the
corpus is walked. That keeps its validation rules cheap to test exhaustively.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


class ScopesConfigError(ValueError):
    """scopes.yaml is missing, malformed, or internally inconsistent."""


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

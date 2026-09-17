"""Persistent Agent -> Combo mapping.

Deliberately a JSON file on disk rather than a MongoDB collection: this repo's
own precedent for admin-managed, low-write-volume configuration is
knowledge/kb-registry.json (see services/mcp/kb/kb_mcp/registry.py) -- a
single versioned file, atomically rewritten, with no separate database
process. Following it here means one fewer piece of infrastructure and one
fewer schema to keep in sync with LibreChat's own Mongo models.

A Combo's targets are references only: an `{endpoint, model}` pair naming an
entry in llm_router.catalog.Catalog, which is itself sourced from
librechat.yaml. Nothing in this file is a secret -- there is no credential
for a Combo to expose, by construction.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: An agent_id is LibreChat's own Agent.id (e.g. "agent_kimi26NOCEngineerV1")
#: and becomes a route segment in the admin API and the OpenAI `model`
#: string a client sends, so it must be safe in a URL path and free of
#: characters that would need escaping in either place.
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

REGISTRY_VERSION = 2


class RegistryError(ValueError):
    """A registry record is malformed or a mutation was refused.

    Raised for unsafe identifiers, targets that don't parse, and mutations
    that would leave the registry inconsistent. Messages are written to be
    shown to the admin verbatim.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_name(name: str) -> None:
    if not name or not SAFE_NAME.match(name):
        raise RegistryError(
            f"invalid id {name!r}: only letters, digits, '-' and '_' are allowed"
        )


@dataclass(frozen=True)
class ComboTarget:
    """One priority-ordered entry in an agent's fallback Combo: a reference
    to an (endpoint, model) pair in the Catalog, plus whether it is
    currently in play. The same endpoint can appear more than once with a
    different model -- that is the whole point of separating the two."""

    endpoint: str
    model: str
    enabled: bool = True

    def to_json(self) -> dict:
        return {"endpoint": self.endpoint, "model": self.model, "enabled": self.enabled}

    @staticmethod
    def from_json(data: dict) -> "ComboTarget":
        return ComboTarget(
            endpoint=data["endpoint"],
            model=data["model"],
            enabled=data.get("enabled", True),
        )


@dataclass(frozen=True)
class Combo:
    """An agent's ordered provider fallback list: index 0 is tried first."""

    agent_id: str
    targets: tuple[ComboTarget, ...]
    enabled: bool = True
    #: Agent.provider/model as they were before this Combo was first
    #: enabled, so the LibreChat-side facade can restore them if the Combo
    #: is disabled or deleted. Opaque to this service: it never reads these
    #: itself, only stores and returns them.
    original_provider: str | None = None
    original_model: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_json(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "targets": [t.to_json() for t in self.targets],
            "enabled": self.enabled,
            "original_provider": self.original_provider,
            "original_model": self.original_model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_json(data: dict) -> "Combo":
        return Combo(
            agent_id=data["agent_id"],
            targets=tuple(ComboTarget.from_json(t) for t in data.get("targets", [])),
            enabled=data.get("enabled", True),
            original_provider=data.get("original_provider"),
            original_model=data.get("original_model"),
            created_at=data.get("created_at", _now()),
            updated_at=data.get("updated_at", _now()),
        )


class Registry:
    """Loads/saves the whole file on every call.

    Simple rather than clever: this is admin-driven CRUD (build a combo,
    reorder its targets) at human speed, not a request-path hot path. A
    process-wide lock is enough to keep concurrent admin edits from racing
    each other; a single instance is what every deployment of this service
    runs.
    """

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._lock = threading.Lock()
        if not self._path.exists():
            self._save({"combos": []})

    # -- persistence -------------------------------------------------

    def _load(self) -> dict:
        if not self._path.exists():
            return {"combos": []}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return {"combos": raw.get("combos", [])}

    def _save(self, data: dict) -> None:
        """Atomic write: tempfile in the same directory, then os.replace.

        A crash mid-write must never leave a truncated/partial registry file
        behind -- os.replace is atomic on both POSIX and Windows, a plain
        write() is not.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "$comment": (
                "NOC LLM Router: per-agent fallback Combos. Managed by the LLM "
                "Routing admin page; hand-edit only if that page is unavailable. "
                "Targets are references (endpoint name + model) into "
                "librechat.yaml -- this file holds no secrets."
            ),
            "version": REGISTRY_VERSION,
            **data,
        }
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".llm-router-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_name, self._path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    # -- combos ----------------------------------------------------------

    def list_combos(self) -> list[Combo]:
        return [Combo.from_json(c) for c in self._load()["combos"]]

    def get_combo(self, agent_id: str) -> Combo | None:
        for c in self.list_combos():
            if c.agent_id == agent_id:
                return c
        return None

    def upsert_combo(
        self,
        *,
        agent_id: str,
        targets: list[dict],
        enabled: bool = True,
        original_provider: str | None = None,
        original_model: str | None = None,
    ) -> Combo:
        """Create or update. Target catalog membership is validated by the
        caller (llm_router.app), which has the Catalog -- this method only
        enforces shape."""
        _check_name(agent_id)
        if not targets:
            raise RegistryError("a combo needs at least one target")
        parsed: list[ComboTarget] = []
        for t in targets:
            endpoint, model = t.get("endpoint"), t.get("model")
            if not endpoint or not model:
                raise RegistryError("each target needs an `endpoint` and a `model`")
            parsed.append(ComboTarget(endpoint=endpoint, model=model, enabled=t.get("enabled", True)))
        with self._lock:
            data = self._load()
            existing = next((c for c in data["combos"] if c["agent_id"] == agent_id), None)
            record = Combo(
                agent_id=agent_id,
                targets=tuple(parsed),
                enabled=enabled,
                original_provider=(
                    original_provider
                    if original_provider is not None
                    else (existing.get("original_provider") if existing else None)
                ),
                original_model=(
                    original_model
                    if original_model is not None
                    else (existing.get("original_model") if existing else None)
                ),
                created_at=existing["created_at"] if existing else _now(),
                updated_at=_now(),
            )
            data["combos"] = [
                c for c in data["combos"] if c["agent_id"] != agent_id
            ] + [record.to_json()]
            self._save(data)
            return record

    def set_combo_enabled(self, agent_id: str, enabled: bool) -> Combo:
        combo = self.get_combo(agent_id)
        if combo is None:
            raise RegistryError(f"no such combo: {agent_id!r}")
        return self.upsert_combo(
            agent_id=combo.agent_id,
            targets=[t.to_json() for t in combo.targets],
            enabled=enabled,
        )

    def delete_combo(self, agent_id: str) -> None:
        with self._lock:
            data = self._load()
            remaining = [c for c in data["combos"] if c["agent_id"] != agent_id]
            if len(remaining) == len(data["combos"]):
                raise RegistryError(f"no such combo: {agent_id!r}")
            data["combos"] = remaining
            self._save(data)

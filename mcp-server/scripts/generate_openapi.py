"""Regenerate the bundled NOC OpenAPI spec from the FastAPI app.

The NOC backend does not serve its OpenAPI spec publicly, so we snapshot it from the app.
Run this whenever the backend's API changes.

Usage (from a Python env that has the *backend's* requirements installed):

    python scripts/generate_openapi.py /path/to/noc-server/noc_backend

It imports ``app.main:app`` (read-only; no server is started, no DB is touched) and writes
``noc_mcp/openapi/noc_openapi.json``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python scripts/generate_openapi.py <path-to/noc_backend>")
    backend_dir = Path(sys.argv[1]).resolve()
    if not (backend_dir / "app" / "main.py").exists():
        sys.exit(f"no app/main.py under {backend_dir}")

    sys.path.insert(0, str(backend_dir))
    os.environ.setdefault("NOC_ENV", "development")

    from app.main import app  # type: ignore

    spec = app.openapi()
    out = Path(__file__).resolve().parent.parent / "noc_mcp" / "openapi" / "noc_openapi.json"
    out.write_text(json.dumps(spec, indent=2), encoding="utf-8")

    paths = spec.get("paths", {})
    gets = sum(1 for p in paths.values() for m in p if m.lower() == "get")
    print(f"wrote {out}  (paths={len(paths)}, GET operations={gets})")


if __name__ == "__main__":
    main()

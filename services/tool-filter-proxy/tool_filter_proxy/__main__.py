"""Entry point: `python -m tool_filter_proxy`."""

from __future__ import annotations

import logging

import uvicorn

from .app import build_app
from .config import settings


def main() -> None:  # pragma: no cover - thin process wiring
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # access_log off: request lines would record full URLs; the proxy emits its own
    # structured line per request (counts and timings, never prompt content).
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port,
                access_log=False, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()

from __future__ import annotations

import uvicorn

from .app import create_app
from .catalog import Catalog
from .config import settings
from .store import Registry

if __name__ == "__main__":
    registry = Registry(settings.data_file)
    catalog = Catalog(
        settings.librechat_api_url,
        internal_secret=settings.internal_secret,
        self_endpoint_name=settings.self_endpoint_name,
    )
    app = create_app(
        registry,
        catalog,
        request_timeout_seconds=settings.request_timeout_seconds,
    )
    uvicorn.run(app, host=settings.host, port=settings.port)

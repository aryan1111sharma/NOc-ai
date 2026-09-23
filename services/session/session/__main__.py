from __future__ import annotations

import uvicorn
from pymongo import MongoClient

from .app import create_app
from .config import settings
from .store import EventStore

if __name__ == "__main__":
    event_store = EventStore(settings.data_file, max_events=settings.max_events)
    mongo_client: MongoClient = MongoClient(settings.mongo_uri)
    db = mongo_client[settings.mongo_db]
    app = create_app(event_store, db, settings=settings)
    uvicorn.run(app, host=settings.host, port=settings.port)

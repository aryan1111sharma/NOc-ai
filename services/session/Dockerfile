# NOC Session Intelligence. Independent, rebuildable/rollback-able service --
# same shape as services/llm-router/Dockerfile, one directory over.
#
# Build context is this directory (not repo root): vendors nothing else in
# the repo.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY session /app/session

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    SESSION_HOST=0.0.0.0 \
    SESSION_PORT=8112 \
    SESSION_DATA_FILE=/data/session-events.json \
    SESSION_MONGO_URI=mongodb://mongodb:27017/LibreChat

EXPOSE 8112

CMD ["python", "-m", "session"]

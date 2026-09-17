# NOC LLM Router. Independent, rebuildable/rollback-able service -- same
# shape as services/tool-filter-proxy/Dockerfile, one directory over.
#
# Build context is this directory (not the repo root): unlike tool-filter-proxy,
# this service vendors nothing from elsewhere in the repo.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY llm_router /app/llm_router

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    LLM_ROUTER_HOST=0.0.0.0 \
    LLM_ROUTER_PORT=8110 \
    LLM_ROUTER_DATA_FILE=/data/llm-router-registry.json

EXPOSE 8110

CMD ["python", "-m", "llm_router"]

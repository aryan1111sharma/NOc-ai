# Tool-filtering proxy. Independent, rebuildable/rollback-able service.
#
# BUILD CONTEXT IS THE REPOSITORY ROOT, not this directory: the image needs the
# platform-agnostic filtering core (filtering/tool_filtering) and the Tool Registry
# (registry/) alongside this service's own code. docker-compose.yml therefore sets
#   context: ../../..   dockerfile: services/tool-filter-proxy/Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY services/tool-filter-proxy/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# The filtering core, copied in as-is. Vendored rather than pip-installed so this image
# is self-contained; it is pure-stdlib and needs no packaging.
COPY filtering/tool_filtering /app/tool_filtering

# The whole registry directory, because tool_registry resolves metadata/, config/, and
# schema/ relative to its own parent (registry/tool_registry/../metadata). Keeping the
# layout intact means no path overrides are needed.
COPY registry /app/registry

COPY services/tool-filter-proxy/tool_filter_proxy /app/tool_filter_proxy

# /app/registry is on the path so `import tool_registry` resolves; /app for the rest.
ENV PYTHONPATH=/app:/app/registry \
    PYTHONUNBUFFERED=1 \
    PROXY_HOST=0.0.0.0 \
    PROXY_PORT=8108

EXPOSE 8108

CMD ["python", "-m", "tool_filter_proxy"]

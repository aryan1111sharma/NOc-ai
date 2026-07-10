FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cms_mcp ./cms_mcp

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8101

EXPOSE 8101

# CMS_BASE_URL / CMS_USER / CMS_PASS must be provided at runtime (env / compose).
CMD ["python", "-m", "cms_mcp"]

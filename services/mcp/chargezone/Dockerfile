FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cz_jira_mcp ./cz_jira_mcp

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8105

EXPOSE 8105

# CZ_JIRA_BASE_URL / CZ_JIRA_EMAIL / CZ_JIRA_API_TOKEN must be provided at runtime (env / compose).
CMD ["python", "-m", "cz_jira_mcp"]

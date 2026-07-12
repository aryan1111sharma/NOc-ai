FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ecog_sd_mcp ./ecog_sd_mcp

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8103 \
    ECOG_COOKIE_FILE=/data/ecog_cookies.json

EXPOSE 8103
VOLUME ["/data"]

# ECOG_EMAIL / ECOG_PASSWORD / ECOG_COOKIE provided at runtime (env / compose).
CMD ["python", "-m", "ecog_sd_mcp"]

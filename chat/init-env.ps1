# Generates chat/.env with fresh random LibreChat secrets.
# After running, edit GLM_API_KEY and NOC_EMAIL / NOC_PASSWORD.
#   Usage (from chat/):  powershell -ExecutionPolicy Bypass -File .\init-env.ps1

$ErrorActionPreference = "Stop"
function Rand([int]$bytes) { -join ((1..$bytes) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) }) }

$envPath = Join-Path $PSScriptRoot ".env"
if (Test-Path $envPath) {
    Write-Host ".env already exists at $envPath - not overwriting. Delete it first to regenerate."
    exit 1
}

$content = @"
HOST=0.0.0.0
PORT=3080
ALLOW_REGISTRATION=true
ALLOW_EMAIL_LOGIN=true
ALLOW_SOCIAL_LOGIN=false
CREDS_KEY=$(Rand 32)
CREDS_IV=$(Rand 16)
JWT_SECRET=$(Rand 32)
JWT_REFRESH_SECRET=$(Rand 32)
MEILI_MASTER_KEY=$(Rand 16)

# ---- Fill these in ----
GLM_API_KEY=your_glm_api_key_here
NOC_BASE_URL=https://www.manthanquench.online
NOC_EMAIL=your-readonly-noc-user@example.com
NOC_PASSWORD=change-me
"@

# ASCII avoids a UTF-8 BOM that would corrupt the first variable for docker compose.
Set-Content -Path $envPath -Value $content -Encoding ascii
Write-Host "Wrote $envPath"
Write-Host "Now edit GLM_API_KEY, NOC_EMAIL and NOC_PASSWORD, then run: docker compose up -d"

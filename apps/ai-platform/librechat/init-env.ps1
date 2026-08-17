# Generates apps/ai-platform/librechat/.env with fresh random LibreChat secrets, or repairs an existing one.
# After a fresh generate, edit GLM_API_KEY and NOC_EMAIL / NOC_PASSWORD.
#
#   Usage (from apps/ai-platform/librechat/):
#     powershell -ExecutionPolicy Bypass -File .\init-env.ps1            # first run: generate
#     powershell -ExecutionPolicy Bypass -File .\init-env.ps1 -Ensure    # existing .env: repair
#
# -Ensure exists because the plain generator refuses to touch an existing .env, so any
# setting introduced after a machine was first set up never reaches it. The stack then
# starts against a .env that is missing a value the compose file now requires, and fails
# in a way that does not name the missing variable. -Ensure is idempotent: it only adds
# keys that are absent and corrects ones that are wrong for this deployment's topology.
# It never prints the value of a pre-existing key, so it is safe to run with output shown.
[CmdletBinding()]
param(
    # Repair an existing .env instead of refusing to touch it.
    [switch]$Ensure
)

$ErrorActionPreference = "Stop"
function Rand([int]$bytes) { -join ((1..$bytes) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) }) }

# The address other machines reach this host on. Picks the adapter that owns the default
# route, which skips Docker/WSL/Hyper-V virtual switches (they have no default gateway)
# and loopback. Falls back to localhost, i.e. host-only access, when there is no LAN.
function Get-PrimaryHostAddress {
    try {
        $cfg = Get-NetIPConfiguration |
            Where-Object { $null -ne $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } |
            Select-Object -First 1
        if ($cfg -and $cfg.IPv4Address.IPAddress) { return $cfg.IPv4Address.IPAddress }
    }
    catch { }
    return 'localhost'
}

$envPath = Join-Path $PSScriptRoot ".env"

if ((Test-Path $envPath) -and -not $Ensure) {
    Write-Host ".env already exists at $envPath - not overwriting."
    Write-Host "To add any settings it is missing, re-run with:  -Ensure"
    Write-Host "To regenerate from scratch, delete it first."
    exit 1
}

if (-not (Test-Path $envPath)) {
    $serverHost = Get-PrimaryHostAddress
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
ADMIN_PANEL_SESSION_SECRET=$(Rand 32)

# ---- Deployment address ----
# The address browsers use to reach this host. Every browser-facing URL is derived from it
# (LibreChat share links, generated-file download links, the admin panel's API URL), so this
# is the only value to change when the deployment moves host or gains a domain name.
SERVER_HOST=$serverHost

# Whether auth cookies carry the Secure flag. MUST be false while serving over plain HTTP:
# browsers refuse to store a Secure cookie from an insecure origin, so the refresh-token
# cookie is silently dropped and users are logged out on every page reload. localhost is
# exempt from that rule, which is why an HTTP deployment looks fine on the Docker host and
# fails for everyone else. Set to true only once TLS terminates in front of the stack.
SESSION_COOKIE_SECURE=false

# ---- Fill these in ----
GLM_API_KEY=your_glm_api_key_here
NOC_BASE_URL=https://www.manthanquench.online
NOC_EMAIL=your-readonly-noc-user@example.com
NOC_PASSWORD=change-me

# ---- Legacy CMS / old NOC (quenchcms.com) ----
CMS_BASE_URL=https://quenchcms.com
CMS_USER=your-cms-username
CMS_PASS=change-me
"@

    # ASCII avoids a UTF-8 BOM that would corrupt the first variable for docker compose.
    Set-Content -Path $envPath -Value $content -Encoding ascii
    Write-Host "Wrote $envPath  (SERVER_HOST=$serverHost)"
    Write-Host "Now edit GLM_API_KEY, NOC_EMAIL and NOC_PASSWORD, then run: .\up.ps1"
    exit 0
}

# ---- -Ensure: repair an existing .env ----
$lines = @(Get-Content -Path $envPath)
$changes = @()

function Get-KeyIndex([string[]]$Lines, [string]$Key) {
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match "^\s*$([regex]::Escape($Key))\s*=") { return $i }
    }
    return -1
}

# SERVER_HOST: required by docker-compose.yml. Absent, every browser-facing URL collapses
# to "http://" and LibreChat will not start.
$idx = Get-KeyIndex $lines 'SERVER_HOST'
if ($idx -lt 0) {
    $detected = Get-PrimaryHostAddress
    $lines += ''
    $lines += '# Address browsers use to reach this host; every browser-facing URL derives from it.'
    $lines += "SERVER_HOST=$detected"
    $changes += "added   SERVER_HOST=$detected  (auto-detected)"
}

# SESSION_COOKIE_SECURE: this stack terminates plain HTTP on port 80 (see nginx.conf), so
# Secure cookies cannot be stored by any browser except on localhost. true here means every
# LAN user is logged out on each reload.
$idx = Get-KeyIndex $lines 'SESSION_COOKIE_SECURE'
if ($idx -lt 0) {
    $lines += ''
    $lines += '# Must stay false while serving plain HTTP; set true only behind TLS.'
    $lines += 'SESSION_COOKIE_SECURE=false'
    $changes += 'added   SESSION_COOKIE_SECURE=false'
}
elseif ($lines[$idx] -notmatch '^\s*SESSION_COOKIE_SECURE\s*=\s*false\s*$') {
    $lines[$idx] = 'SESSION_COOKIE_SECURE=false'
    $changes += 'fixed   SESSION_COOKIE_SECURE=false  (was truthy; breaks login over plain HTTP)'
}

if ($changes.Count -eq 0) {
    Write-Host "$envPath already has everything this stack requires - no changes."
    exit 0
}

Copy-Item -Path $envPath -Destination "$envPath.bak" -Force
Set-Content -Path $envPath -Value $lines -Encoding ascii
Write-Host "Repaired $envPath  (previous copy saved as .env.bak)"
$changes | ForEach-Object { Write-Host "  $_" }
Write-Host "Apply with:  docker compose up -d --force-recreate api docgen-mcp-server admin-panel"

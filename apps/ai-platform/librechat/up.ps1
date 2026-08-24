#!/usr/bin/env pwsh
# Start the Ador Manthan.AI stack with the version + commit id wired into the
# LibreChat footer. Reads the version from ../VERSION and the commit from
# `git rev-parse --short HEAD`, then runs `docker compose up -d --build`.
#
# LibreChat inspects each MCP server exactly once, at its own startup. A rebuild
# recreates the MCP containers but leaves the api container running, so without a
# follow-up restart LibreChat keeps handles on containers that no longer exist and
# comes back reporting 0 tools - every integration silently missing from the UI,
# with no error shown to the user. So once the build is done we wait for the MCP
# servers to accept connections and then restart api, in that order.
#
# Usage:
#   ./up.ps1                 # build + start everything, then wait + restart api
#   ./up.ps1 api             # extra args pass through to docker compose up; the
#                            # wait + restart step is skipped
$ErrorActionPreference = 'Stop'
$chatDir = $PSScriptRoot
# Repo root is 3 levels up: apps/ai-platform/librechat -> apps/ai-platform -> apps -> repo root.
$root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $chatDir))

$env:APP_VERSION = (Get-Content (Join-Path $root 'VERSION') -Raw).Trim()
$env:GIT_SHA = (git -C $root rev-parse --short HEAD).Trim()

# Read SERVER_HOST from .env so the closing "Open http://..." message is always right,
# instead of a hardcoded address that goes stale the moment the deployment moves host.
$serverHost = 'localhost'
$envFile = Join-Path $chatDir '.env'
if (Test-Path $envFile) {
    $match = Select-String -Path $envFile -Pattern '^SERVER_HOST=(.+)$' | Select-Object -First 1
    if ($match) { $serverHost = $match.Matches[0].Groups[1].Value.Trim() }
}

# compose service name -> the port it listens on inside the compose network.
$mcpServers = [ordered]@{
    'noc-mcp-server'             = 8100
    'cms-mcp-server'             = 8101
    'jira-mcp-server'            = 8102
    'docgen-mcp-server'          = 8103
    'ecog-sd-mcp-server'         = 8104
    'chargezone-jira-mcp-server' = 8105
    'kb-mcp-server'              = 8106
    'platform-mcp-server'        = 8107
}
# kb-mcp-server downloads its embedding models on first run, which can take
# several minutes - hence the generous budget.
$readyTimeoutSeconds = 600

# True once the service accepts a TCP connection on its MCP port. Runs with
# ErrorActionPreference=Continue and all streams suppressed: while the container
# is still booting, `exec` writes to stderr, which Windows PowerShell 5.1 would
# otherwise turn into a terminating NativeCommandError.
function Test-ServiceReady {
    param([string]$Service, [int]$Port)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        docker compose exec -T $Service python -c "import socket; socket.create_connection(('127.0.0.1', $Port), 2).close()" *> $null
        return ($LASTEXITCODE -eq 0)
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

Write-Host "Starting Ador Manthan.AI v$($env:APP_VERSION) ($($env:GIT_SHA))"
Push-Location $chatDir
try {
    docker compose up -d --build @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed (exit $LASTEXITCODE)" }

    if ($args.Count -gt 0) {
        Write-Host "Extra args given - skipping the MCP readiness wait and the api restart."
    }
    else {
        Write-Host "Waiting for the MCP servers to accept connections..."
        $deadline = (Get-Date).AddSeconds($readyTimeoutSeconds)
        $notReady = @()
        foreach ($service in $mcpServers.Keys) {
            $port = $mcpServers[$service]
            $ready = $false
            while (-not $ready -and (Get-Date) -lt $deadline) {
                $ready = Test-ServiceReady -Service $service -Port $port
                if (-not $ready) { Start-Sleep -Seconds 5 }
            }
            if ($ready) {
                Write-Host "  ready: $service"
            }
            else {
                $notReady += $service
                Write-Warning "  NOT ready after $readyTimeoutSeconds s: $service"
            }
        }

        # LibreChat must come up after the MCP servers, or it will hold dead
        # handles and report 0 tools.
        Write-Host "Restarting LibreChat so it re-handshakes with the MCP servers..."
        docker compose restart api
        if ($LASTEXITCODE -ne 0) { throw "docker compose restart api failed (exit $LASTEXITCODE)" }

        if ($notReady.Count -gt 0) {
            Write-Warning "These MCP servers never came up: $($notReady -join ', '). LibreChat will be missing their tools; check 'docker compose logs <service>'."
        }
    }
}
finally {
    Pop-Location
}
Write-Host "Open http://$serverHost  - footer shows v$($env:APP_VERSION) ($($env:GIT_SHA))"
Write-Host "Admin panel: http://${serverHost}:3091"
Write-Host "Verify the tool count with: docker compose logs api | Select-String 'Initialized with'"

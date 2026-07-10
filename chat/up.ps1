#!/usr/bin/env pwsh
# Start the Manthan AI stack with the version + commit id wired into the
# LibreChat footer. Reads the version from ../VERSION and the commit from
# `git rev-parse --short HEAD`, then runs `docker compose up -d --build`.
#
# Usage:
#   ./up.ps1                 # build + start everything
#   ./up.ps1 api             # (extra args are passed through to docker compose up)
$ErrorActionPreference = 'Stop'
$chatDir = $PSScriptRoot
$root = Split-Path -Parent $chatDir

$env:APP_VERSION = (Get-Content (Join-Path $root 'VERSION') -Raw).Trim()
$env:GIT_SHA = (git -C $root rev-parse --short HEAD).Trim()

Write-Host "Starting Manthan AI v$($env:APP_VERSION) ($($env:GIT_SHA))"
Push-Location $chatDir
try {
    docker compose up -d --build @args
} finally {
    Pop-Location
}
Write-Host "Open http://localhost:3080  — footer shows v$($env:APP_VERSION) ($($env:GIT_SHA))"

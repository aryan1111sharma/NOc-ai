#!/usr/bin/env pwsh
# Bump the project version. Updates VERSION and the LibreChat welcome message,
# regenerates CHANGELOG.md (git-cliff, with a dated vX.Y.Z section whose entries
# link to their commit ids), commits as `chore(release): vX.Y.Z`, and tags it.
# Does NOT push — review, then:  git push && git push origin vX.Y.Z
#
# Usage:
#   ./scripts/bump-version.ps1 patch      # 0.1.0 -> 0.1.1
#   ./scripts/bump-version.ps1 minor      # 0.1.0 -> 0.2.0
#   ./scripts/bump-version.ps1 major      # 0.1.0 -> 1.0.0
#   ./scripts/bump-version.ps1 1.2.3      # explicit version
[CmdletBinding()]
param([Parameter(Mandatory)][string]$Bump)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$versionFile = Join-Path $root 'VERSION'
$yamlFile = Join-Path $root 'apps/ai-platform/librechat/librechat.yaml'

$current = (Get-Content $versionFile -Raw).Trim()
if ($current -notmatch '^\d+\.\d+\.\d+$') { throw "VERSION malformed: '$current'" }
$p = $current.Split('.') | ForEach-Object { [int]$_ }

switch ($Bump) {
    'major' { $new = "$($p[0] + 1).0.0" }
    'minor' { $new = "$($p[0]).$($p[1] + 1).0" }
    'patch' { $new = "$($p[0]).$($p[1]).$($p[2] + 1)" }
    default {
        if ($Bump -match '^\d+\.\d+\.\d+$') { $new = $Bump }
        else { throw "Bump must be 'major', 'minor', 'patch', or X.Y.Z (got '$Bump')" }
    }
}
if (git -C $root tag -l "v$new") { throw "Tag v$new already exists" }
Write-Host "Bumping $current -> $new"

# 1. VERSION file (single source of truth).
Set-Content -Path $versionFile -Value $new -Encoding ascii

# 2. Version token in the LibreChat welcome line (footer version comes from git at runtime).
$lines = Get-Content $yamlFile | ForEach-Object {
    if ($_ -match 'customWelcome') { $_ -replace 'v\d+\.\d+\.\d+', "v$new" } else { $_ }
}
Set-Content -Path $yamlFile -Value $lines -Encoding ascii

# 3. Regenerate the changelog with the new version section, commit, and tag.
Push-Location $root
try {
    npx --yes git-cliff@latest --tag "v$new" -o CHANGELOG.md
    git add VERSION apps/ai-platform/librechat/librechat.yaml CHANGELOG.md
    git commit -m "chore(release): v$new"
    git tag "v$new"
}
finally {
    Pop-Location
}
Write-Host "Committed + tagged v$new. Review, then push:  git push && git push origin v$new"

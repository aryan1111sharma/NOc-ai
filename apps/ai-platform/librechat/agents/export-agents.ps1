#!/usr/bin/env pwsh
# Export LibreChat agents to agents.json (config-as-code). Run after changing an agent in the
# UI so the change is captured in git. Requires the mongo container (noc-librechat-mongo) up.
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$js = Get-Content "$dir\_export.js" -Raw
$json = $js | docker exec -i noc-librechat-mongo mongosh --quiet LibreChat --file /dev/stdin
# UTF-8 without BOM so the JSON round-trips cleanly through restore.
[System.IO.File]::WriteAllText("$dir\agents.json", (($json -join "`n") + "`n"))
Write-Host "Exported agents -> $dir\agents.json"

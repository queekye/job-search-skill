$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$projectRoot = Split-Path -Parent $PSScriptRoot
$codexCommand = Get-Command codex -ErrorAction SilentlyContinue
if (-not $codexCommand) { throw 'Codex CLI with plugin support is required on PATH. Open a Codex terminal or install a supported Codex CLI.' }
& $codexCommand.Source plugin marketplace add --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'This Codex CLI does not support local plugin marketplaces. Update Codex before installing.' }
& $codexCommand.Source plugin add --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'This Codex CLI does not support plugin installation. Update Codex before installing.' }
$snapshotRoot = $projectRoot
if (-not (Test-Path -LiteralPath (Join-Path $snapshotRoot 'INSTALL-MANIFEST.json'))) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'build_release.py'))) {
        throw 'Installation snapshot is missing. Extract a fresh reviewed runtime ZIP.'
    }
    Write-Host 'Building a runtime snapshot from this source checkout.'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start.ps1') package
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $snapshotRoot = Join-Path $projectRoot 'dist\marketplace'
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start.ps1') verify $snapshotRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

function Get-ProjectMarketplace {
    $listing = & $codexCommand.Source plugin marketplace list --json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect existing plugin marketplaces; no source was replaced.' }
    $projectMarkets = @((($listing -join "`n") | ConvertFrom-Json).marketplaces | Where-Object { $_.name -eq 'job-search-local' })
    if ($projectMarkets.Count -gt 1) { throw 'The project marketplace has ambiguous sources; no source was replaced.' }
    if ($projectMarkets.Count -eq 1) { return $projectMarkets[0] }
    return $null
}

$previousRoot = $null
$existing = Get-ProjectMarketplace
if ($existing) {
    if (-not (Test-Path -LiteralPath $existing.root -PathType Container)) {
        throw "Previous project marketplace directory '$($existing.root)' is unavailable. Restore its runtime ZIP at the registered path or remove only job-search-local before retrying; its mapping was kept."
    }
    $oldRoot = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $existing.root).Path)
    $newRoot = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $snapshotRoot).Path)
    if ($oldRoot -ne $newRoot) {
        $oldMarket = Get-Content -LiteralPath (Join-Path $oldRoot '.agents\plugins\marketplace.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $oldPlugins = @($oldMarket.plugins)
        if ($oldMarket.name -ne 'job-search-local' -or $oldPlugins.Count -ne 1 -or
            $oldPlugins[0].name -ne 'job-search-skill' -or $oldPlugins[0].source.source -ne 'local') {
            throw 'An unrelated marketplace uses job-search-local; its source was not changed.'
        }
        $relative = [string]$oldPlugins[0].source.path
        if ([string]::IsNullOrWhiteSpace($relative) -or [IO.Path]::IsPathRooted($relative) -or
            $relative.Contains(':') -or ($relative -split '[\\/]') -contains '..') {
            throw 'The existing project marketplace has an unsafe plugin path; its source was not changed.'
        }
        $oldPluginRoot = [IO.Path]::GetFullPath((Join-Path $oldRoot $relative))
        if ($oldPluginRoot -ne $oldRoot -and -not $oldPluginRoot.StartsWith($oldRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'The existing plugin path escapes its marketplace; its source was not changed.'
        }
        $oldPlugin = Get-Content -LiteralPath (Join-Path $oldPluginRoot '.codex-plugin\plugin.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($oldPlugin.name -ne 'job-search-skill' -or $oldPlugin.skills -ne './skills/' -or
            $oldPlugin.mcpServers -ne './.mcp.json' -or $oldPlugin.author.name -ne 'Job Search Skill contributors') {
            throw 'The existing marketplace cannot be identified as this project; its source was not changed.'
        }
        if (Test-Path -LiteralPath (Join-Path $oldPluginRoot 'INSTALL-MANIFEST.json')) {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start.ps1') verify $oldPluginRoot
            if ($LASTEXITCODE -ne 0) { throw 'The previous project snapshot failed verification; its source was not changed.' }
        }
        $previousRoot = $oldRoot
    }
}

$sourceRemoved = $false
try {
    if ($previousRoot) {
        & $codexCommand.Source plugin marketplace remove 'job-search-local'
        if ($LASTEXITCODE -ne 0) { throw 'The previous project marketplace could not be removed.' }
        $sourceRemoved = $true
    }
    & $codexCommand.Source plugin marketplace add $snapshotRoot
    if ($LASTEXITCODE -ne 0) { throw 'The new project marketplace could not be registered.' }
    & $codexCommand.Source plugin add 'job-search-skill@job-search-local'
    if ($LASTEXITCODE -ne 0) { throw 'The project plugin could not be installed.' }
} catch {
    $installFailure = $_
    if ($sourceRemoved) {
        if (Get-ProjectMarketplace) {
            & $codexCommand.Source plugin marketplace remove 'job-search-local'
            if ($LASTEXITCODE -ne 0) { throw 'Installation failed and its marketplace could not be rolled back.' }
        }
        & $codexCommand.Source plugin marketplace add $previousRoot
        if ($LASTEXITCODE -ne 0) { throw 'Installation failed and the previous project source could not be restored.' }
        & $codexCommand.Source plugin add 'job-search-skill@job-search-local'
        if ($LASTEXITCODE -ne 0) { throw 'The previous source was restored, but its plugin could not be re-enabled.' }
        Write-Host 'Restored the previous project marketplace and plugin.'
    }
    throw $installFailure
}
Write-Host 'Installed. Start a new Codex task and use $career.'

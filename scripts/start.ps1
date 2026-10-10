param([ValidateSet('serve','mcp','backup','restore','check','package','verify')][string]$Mode = 'serve', [Parameter(ValueFromRemainingArguments=$true)][string[]]$ForwardArgs)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$candidates = @(
    $env:JOB_SEARCH_PYTHON,
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
)
$pythonExe = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if (-not $pythonExe) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonExe = $pythonCommand.Source }
}
if (-not $pythonExe) { throw 'Python 3.11+ is required. Set JOB_SEARCH_PYTHON to an installed interpreter.' }
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& $pythonExe -X utf8 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required. Set JOB_SEARCH_PYTHON to a supported interpreter.' }
if ($Mode -eq 'verify') {
    & $pythonExe -X utf8 (Join-Path $projectRoot 'scripts\verify_release.py') @ForwardArgs
    exit $LASTEXITCODE
}
if (($Mode -eq 'package' -and -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'build_release.py'))) -or
    ($Mode -eq 'check' -and -not (Test-Path -LiteralPath (Join-Path $projectRoot 'tests')))) {
    throw 'Development checks and packaging are available in the source repository.'
}
& $pythonExe -X utf8 (Join-Path $projectRoot 'scripts\run.py') $Mode @ForwardArgs
exit $LASTEXITCODE

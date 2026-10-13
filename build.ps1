param([switch]$SkipChecks)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not $SkipChecks) {
        python scripts/check_publication.py
        if ($LASTEXITCODE -ne 0) { throw 'Publication safety check failed.' }
        python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    }
    python -m PyInstaller --noconfirm xCAL.spec
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
    Write-Host 'Community app: dist/xCAL-community/xCAL.exe'
    Write-Host 'OAuth configuration and team data are never bundled.'
} finally {
    Pop-Location
}

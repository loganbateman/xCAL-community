param(
    [switch]$SkipChecks,
    [string]$OAuthClient,
    [switch]$Package
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $communityPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $communityPython)) { throw 'Create the Community .venv first.' }
    if ($Package -and -not $OAuthClient) { throw 'A beta package requires the Community Desktop OAuth client.' }
    if (-not $SkipChecks) {
        & $communityPython scripts/check_publication.py
        if ($LASTEXITCODE -ne 0) { throw 'Publication safety check failed.' }
        & $communityPython -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
        & $communityPython -m pyflakes src scripts tests
        if ($LASTEXITCODE -ne 0) { throw 'Lint failed.' }
        & $communityPython scripts/smoke_test_ui.py
        if ($LASTEXITCODE -ne 0) { throw 'UI smoke failed.' }
        & $communityPython scripts/smoke_test_ui.py --recovery
        if ($LASTEXITCODE -ne 0) { throw 'Recovery smoke failed.' }
    }
    if ($OAuthClient) {
        $resolvedClient = (Resolve-Path -LiteralPath $OAuthClient -ErrorAction Stop).Path
        $env:XCAL_PUBLIC_OAUTH_CLIENT_FILE = $resolvedClient
    }
    try {
        & $communityPython -m PyInstaller --noconfirm xCAL.spec
        if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
    } finally {
        Remove-Item Env:XCAL_PUBLIC_OAUTH_CLIENT_FILE -ErrorAction SilentlyContinue
    }
    Write-Host 'Community app: dist/xCAL-community/xCAL.exe'
    $smokeArgs = @('--smoke-test')
    if ($OAuthClient) { $smokeArgs += '--expect-oauth-client' }
    $smokeProcess = Start-Process -FilePath (Join-Path $PSScriptRoot 'dist\xCAL-community\xCAL.exe') -ArgumentList $smokeArgs -WindowStyle Hidden -PassThru
    if (-not $smokeProcess.WaitForExit(45000)) {
        $smokeProcess.Kill()
        throw 'Packaged app smoke test timed out.'
    }
    if ($smokeProcess.ExitCode -ne 0) { throw 'Packaged app smoke test failed.' }
    if ($Package) {
        & $communityPython scripts/package_beta.py
        if ($LASTEXITCODE -ne 0) { throw 'Beta archive packaging failed.' }
    }
    if ($OAuthClient) {
        Write-Host 'Community Desktop OAuth client injected; user tokens remain outside the install.'
    } else {
        Write-Warning 'No community OAuth client was injected; public Google sign-in will be unavailable.'
    }
} finally {
    Pop-Location
}

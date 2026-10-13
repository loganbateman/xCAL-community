param(
    [string]$Destination = (Join-Path $PSScriptRoot "..\build\vendor\aim_xrk")
)

$ErrorActionPreference = "Stop"
$url = "https://www.aim-sportline.com/aim-software-betas/DLL/TestMatLabXRK.zip"
$expectedSha256 = "6b422272e7e0df0b9a262376b56de8d53492a1c4a239410031b133182dbcab97"
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("xcal-aim-sdk-" + [guid]::NewGuid().ToString("N"))
$archive = "$tempRoot.zip"

New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
try {
    # AiM's server occasionally closes this download early. curl's resume and
    # retry behavior makes release builds deterministic, then the pinned hash
    # ensures no unreviewed native binary enters xCAL.
    & curl.exe -L --fail --retry 4 --retry-all-errors -o $archive $url
    if ($LASTEXITCODE -ne 0) { throw "AiM XRK SDK download failed." }
    $actualSha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        throw "AiM XRK SDK checksum did not match the reviewed package."
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $tempRoot
    $source = Join-Path $tempRoot "TestMatLabXRK"
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $source "DLL-2022\MatLabXRK-2022-64-ReleaseU.dll") -Destination $Destination -Force
    foreach ($dependency in "libiconv-2.dll", "libxml2-2.dll", "libz.dll", "pthreadVC2_x64.dll") {
        Copy-Item -LiteralPath (Join-Path $source "64\$dependency") -Destination $Destination -Force
    }
} finally {
    Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

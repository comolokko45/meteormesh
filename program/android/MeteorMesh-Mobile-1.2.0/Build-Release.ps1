$ErrorActionPreference = 'Stop'

$sdk = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
$signDir = Join-Path $env:LOCALAPPDATA 'MeteorMeshMobile\signing'
$storePath = Join-Path $signDir 'meteormesh-mobile-release.p12'
$secretPath = Join-Path $signDir 'keystore-password.xml'

if (-not (Test-Path -LiteralPath $storePath)) { throw 'Signing key is missing. Run New-SigningKey.ps1 once.' }
if (-not (Test-Path -LiteralPath $secretPath)) { throw 'Protected signing password is missing.' }

$securePassword = Import-Clixml -LiteralPath $secretPath
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $env:METEORMESH_SIGNING_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$env:METEORMESH_SIGNING_STORE = $storePath
$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
Push-Location $PSScriptRoot
try {
    & .\gradlew.bat --no-daemon assembleRelease
    if ($LASTEXITCODE -ne 0) { throw "Gradle build failed with exit code $LASTEXITCODE" }

    $outDir = Join-Path $PSScriptRoot 'dist'
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $built = Join-Path $PSScriptRoot 'app\build\outputs\apk\release\app-release.apk'
    $apk = Join-Path $outDir 'MeteorMesh-Mobile-1.2.0.apk'
    Copy-Item -LiteralPath $built -Destination $apk -Force
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $apk).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$apk.sha256" -Value "$hash  MeteorMesh-Mobile-1.2.0.apk" -Encoding ascii

    $apksigner = Join-Path $sdk 'build-tools\35.0.0\apksigner.bat'
    & $apksigner verify --verbose --print-certs $apk
    if ($LASTEXITCODE -ne 0) { throw "APK signature verification failed with exit code $LASTEXITCODE" }
    Write-Output "APK: $apk"
    Write-Output "SHA-256: $hash"
}
finally {
    Pop-Location
    $env:METEORMESH_SIGNING_PASSWORD = $null
    $env:METEORMESH_SIGNING_STORE = $null
}

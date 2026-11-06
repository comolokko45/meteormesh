$ErrorActionPreference = 'Stop'

$signDir = Join-Path $env:LOCALAPPDATA 'MeteorMeshMobile\signing'
$storePath = Join-Path $signDir 'meteormesh-mobile-release.p12'
$secretPath = Join-Path $signDir 'keystore-password.xml'
$keytool = Join-Path $env:JAVA_HOME 'bin\keytool.exe'

if (Test-Path -LiteralPath $storePath) { throw 'Signing key already exists; refusing to replace it.' }
if (Test-Path -LiteralPath $secretPath) { throw 'Protected password file already exists; refusing to replace it.' }
if (-not (Test-Path -LiteralPath $keytool)) { throw 'JDK keytool not found. Set JAVA_HOME to the installed JDK.' }

New-Item -ItemType Directory -Force -Path $signDir | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = Get-Acl -LiteralPath $signDir
$acl.SetAccessRuleProtection($true, $false)
$rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
    $identity,
    [System.Security.AccessControl.FileSystemRights]::FullControl,
    [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit,
    [System.Security.AccessControl.PropagationFlags]::None,
    [System.Security.AccessControl.AccessControlType]::Allow
)
$acl.SetAccessRule($rule)
Set-Acl -LiteralPath $signDir -AclObject $acl

$bytes = [byte[]]::new(48)
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$password = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$securePassword = ConvertTo-SecureString -String $password -AsPlainText -Force
$securePassword | Export-Clixml -LiteralPath $secretPath

try {
    $env:METEORMESH_SIGNING_PASSWORD = $password
    & $keytool -genkeypair -alias meteormesh-mobile -keyalg RSA -keysize 3072 -sigalg SHA256withRSA `
        -validity 10000 -storetype PKCS12 -keystore $storePath `
        -storepass:env METEORMESH_SIGNING_PASSWORD -keypass:env METEORMESH_SIGNING_PASSWORD `
        -dname 'CN=MeteorMesh Mobile, OU=Monitoring, O=MeteorMesh, C=TR'
    if ($LASTEXITCODE -ne 0) { throw "keytool failed with exit code $LASTEXITCODE" }
}
finally {
    $env:METEORMESH_SIGNING_PASSWORD = $null
    $password = $null
    $securePassword = $null
    [Array]::Clear($bytes, 0, $bytes.Length)
}

Write-Output "Release signing key created at $storePath"
Write-Output "Password is DPAPI-protected for this Windows user at $secretPath"

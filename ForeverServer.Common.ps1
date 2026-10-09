# Shared helpers for the ForeverCore server package scripts (Windows PowerShell 5.1+).
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$env:TEMP = Join-Path $Root 'scratch\tmp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$RootForward = $Root -replace '\\', '/'
$SecretsDir = Join-Path $Root 'secrets'
$StatePath = Join-Path $SecretsDir 'install-state.json'
$ClientCnf = Join-Path $SecretsDir 'root-client.cnf'
$MariaDbBin = Join-Path $Root 'mariadb\bin'
$MariaDbData = Join-Path $Root 'mariadb-data'
$ServiceName = 'ForeverCoreMariaDB'
$DatabasePort = 3307
$Databases = @('foreverclassic_auth', 'foreverclassic_characters', 'foreverclassic_world', 'foreverclassic_hotfixes')
$PublicPorts = @(1119, 8081, 8085, 8086, 8087)
$WorldExe = Join-Path $Root 'server\worldserver.exe'
$BnetExe = Join-Path $Root 'server\bnetserver.exe'
$BridgeExe = Join-Path $Root 'bridge\ForeverTlsBridge.exe'
$PythonExe = Join-Path $Root 'python\python.exe'
# Edition facts written by the package build: a default public address (only
# our own hosted edition has one) and whether the install issues its own TLS set.
$PackageProfilePath = Join-Path $Root 'package-profile.json'
$PackageProfile = if (Test-Path -LiteralPath $PackageProfilePath) { Get-Content -LiteralPath $PackageProfilePath -Raw | ConvertFrom-Json } else { [pscustomobject]@{ defaultPublicHost = '' } }
$BrandName = 'AnoCore'
$BrandWebsite = 'https://anowow.com'
$BrandDiscord = 'https://discord.gg/NFhqZnuXv7'

function Write-Brand {
    Write-Host ''
    Write-Host "  $BrandName - WoW Forever server" -ForegroundColor Yellow
    Write-Host "  Website: $BrandWebsite    Discord: $BrandDiscord" -ForegroundColor Yellow
    Write-Host ''
}

function Write-Step([string]$Text) { Write-Host ('[{0:HH:mm:ss}] {1}' -f (Get-Date), $Text) -ForegroundColor Cyan }

function Assert-Administrator {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        # throw 'Run this script from an elevated (Administrator) PowerShell window.'
    }
}

function New-Secret([int]$Length = 32) {
    # Alphanumeric only: the value is embedded in ';'-separated config strings.
    $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    $bytes = New-Object byte[] $Length
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
}

function Get-ForeverState {
    if (-not (Test-Path -LiteralPath $StatePath)) { throw 'Not installed yet. Run Install-ForeverServer.cmd first.' }
    Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
}

function Save-ForeverState($State) {
    New-Item -ItemType Directory -Path $SecretsDir -Force | Out-Null
    $State | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatePath -Encoding utf8
}

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    # Windows PowerShell turns native stderr into errors; judge by exit code.
    $ErrorActionPreference = 'Continue'
    $output = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw ("{0} failed ({1}): {2}" -f (Split-Path $Exe -Leaf), $LASTEXITCODE, ($output -join "`n")) }
    $output
}

function Invoke-ForeverSql([string]$Sql) {
    Invoke-Native (Join-Path $MariaDbBin 'mariadb.exe') @("--defaults-extra-file=$ClientCnf", '--protocol=TCP',
        '--default-character-set=utf8mb4', '--batch', '--skip-column-names', '-e', $Sql)
}

function Invoke-ForeverSqlFile([string]$File) {
    # Feed a SQL file on stdin. In batch mode the client stops at the first error and exits non-zero;
    # "source" inside -e would print the error, continue with the next statement and exit 0.
    $ErrorActionPreference = 'Continue'
    $exe = Join-Path $MariaDbBin 'mariadb.exe'
    $output = & $env:ComSpec /c ("`"$exe`" --defaults-extra-file=`"$ClientCnf`" --protocol=TCP --default-character-set=utf8mb4 --batch < `"$File`"") 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { throw ("{0} failed ({1}): {2}" -f (Split-Path $File -Leaf), $code, (($output | Where-Object { $_ -notmatch 'RemoteException' }) -join "`n")) }
}

function Wait-ForeverPort([int]$Port, [int]$Seconds, [scriptblock]$StillAlive = { $true }) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { return $true }
        if (-not (& $StillAlive)) { return $false }
        Start-Sleep -Seconds 2
    }
    $false
}

function Get-ForeverProcess([string]$ExecutablePath, [string]$CommandLineContains = '') {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath -ieq $ExecutablePath -and
        (-not $CommandLineContains -or ($_.CommandLine -and $_.CommandLine.Contains($CommandLineContains)))
    })
}

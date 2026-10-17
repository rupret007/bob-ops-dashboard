#Requires -Version 5.1
<#
.SYNOPSIS
  Windows join helper: runs the bash installer inside WSL Ubuntu.
#>
param(
    # Prefer passing the code via $env:BOB_JOIN_CODE (what bobctl prints) so it
    # stays out of the process list and shell history.
    [string]$Code = $env:BOB_JOIN_CODE,
    [string]$Ref,
    [string]$Sha256,
    [int]$Lanes = 0,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if ($Ref -cnotmatch '^[0-9a-fA-F]{40}$' -or $Sha256 -cnotmatch '^[0-9a-fA-F]{64}$') {
    throw 'A full 40-hex Ref and 64-hex archive Sha256 are required.'
}
if (-not $Code) {
    $secureCode = Read-Host 'Join code (hidden)' -AsSecureString
    $Code = [System.Net.NetworkCredential]::new('', $secureCode).Password
}
# wsl.exe prints UTF-16; ask for UTF-8 and strip stray NULs so matching works on PS 5.1.
$env:WSL_UTF8 = '1'

if ([Environment]::UserName -eq 'SYSTEM') {
    Write-Error 'Run join.ps1 as a normal user, not SYSTEM.'
    exit 1
}

$ubuntu = (& wsl -l -q 2>$null | ForEach-Object { ($_ -replace "`0", '').Trim() } |
    Where-Object { $_ -match '^Ubuntu' } | Select-Object -First 1)
if (-not $ubuntu) {
    Write-Host 'Run: wsl --install -d Ubuntu  then reboot and re-run this command.'
    exit 1
}
$distro = $ubuntu.Trim()

$ts = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $ts) {
    $tsExe = "${env:ProgramFiles}\Tailscale\tailscale.exe"
    if (Test-Path -LiteralPath $tsExe) {
        $ts = $tsExe
    }
}
if (-not $ts) {
    Write-Warning 'Tailscale not found on Windows. Install from https://tailscale.com/download (WSL join still needs tailnet access).'
}

$lanesArg = ''
if ($Lanes -gt 0) {
    $lanesArg = " --lanes $Lanes"
}

$joinUrl = "https://raw.githubusercontent.com/rupret007/bob-ops-dashboard/$Ref/cluster/install/join.sh"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if ($DryRun) {
    Write-Host "DRY: wsl -d $distro -- bash -lc (BOB_JOIN_CODE via WSLENV)"
    Write-Host "DRY: set env:BOB_JOIN_CODE and WSLENV=BOB_JOIN_CODE/u"
    Write-Host "DRY: curl join.sh | bash -s -- --ref $Ref --sha256 $Sha256$lanesArg"
    $wslUser = (& wsl -d $distro -- whoami).Trim()
    Write-Host "DRY: wsl -d $distro -u root loginctl enable-linger $wslUser"
    $taskRun = "wsl.exe -d $distro --exec /usr/bin/sleep infinity"
    if ($isAdmin) {
        Write-Host "DRY: schtasks /Create /TN Bob-Cluster-WSL /SC ONLOGON /TR `"$taskRun`" /RL HIGHEST /F"
    } else {
        Write-Host "DRY: schtasks /Create /TN Bob-Cluster-WSL /SC ONLOGON /TR `"$taskRun`" /F"
    }
    exit 0
}

$env:BOB_JOIN_CODE = $Code
if ($env:WSLENV) { $env:WSLENV = "$($env:WSLENV):BOB_JOIN_CODE/u" } else { $env:WSLENV = 'BOB_JOIN_CODE/u' }

$bashCmd = "curl -fsSL '$joinUrl' | bash -s -- --ref '$Ref' --sha256 '$Sha256'$lanesArg"
& wsl -d $distro -- bash -lc $bashCmd
$rc = $LASTEXITCODE
Remove-Item Env:BOB_JOIN_CODE -ErrorAction SilentlyContinue
if ($rc -ne 0) {
    exit $rc
}

$wslUser = (& wsl -d $distro -- whoami).Trim()
& wsl -d $distro -u root -- loginctl enable-linger $wslUser 2>$null

$taskName = 'Bob-Cluster-WSL'
$taskRun = "wsl.exe -d $distro --exec /usr/bin/sleep infinity"
$schtasksArgs = @(
    '/Create', '/TN', $taskName, '/SC', 'ONLOGON', '/TR', $taskRun, '/F'
)
if ($isAdmin) {
    $schtasksArgs += '/RL', 'HIGHEST'
}
& schtasks.exe @schtasksArgs | Out-Null

exit 0

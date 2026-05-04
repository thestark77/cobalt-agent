#Requires -Version 5.1
<#
.SYNOPSIS
    cobalt-agent installer for Windows (runs via WSL)
.DESCRIPTION
    Installs Hermes Agent + cobalt-routing plugin inside WSL.
    Hermes requires Linux — this script bootstraps WSL if needed and delegates to install.sh.
.PARAMETER Distribution
    WSL distribution name (default: Ubuntu)
.PARAMETER SkipHermes
    Skip Hermes installation (only install plugin + config)
.EXAMPLE
    .\install.ps1
    .\install.ps1 -Distribution "Ubuntu-24.04"
#>

param(
    [string]$Distribution = "",
    [switch]$SkipHermes
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  cobalt-agent — Windows Installer (via WSL)" -ForegroundColor Cyan
Write-Host ""

# Check WSL
$wslAvailable = $false
try {
    $wslOutput = wsl --status 2>&1
    $wslAvailable = $true
} catch {
    $wslAvailable = $false
}

if (-not $wslAvailable) {
    Write-Host "  WSL is not available." -ForegroundColor Red
    Write-Host "  Install WSL first: wsl --install" -ForegroundColor Yellow
    Write-Host "  Then re-run this script." -ForegroundColor Yellow
    exit 1
}

Write-Host "  WSL detected" -ForegroundColor Green

# Determine distribution
if ($Distribution) {
    $distroArg = "-d $Distribution"
    Write-Host "  Using distribution: $Distribution" -ForegroundColor Cyan
} else {
    $distroArg = ""
    Write-Host "  Using default WSL distribution" -ForegroundColor Cyan
}

# Find install.sh
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$installSh = Join-Path $scriptDir "install.sh"

if (-not (Test-Path $installSh)) {
    Write-Host "  install.sh not found at $installSh" -ForegroundColor Red
    Write-Host "  Clone the repo first: git clone https://github.com/thestark77/cobalt-agent.git" -ForegroundColor Yellow
    exit 1
}

# Convert Windows path to WSL path
$wslPath = wsl wslpath -u ($installSh -replace '\\', '/')
Write-Host "  Install script: $wslPath" -ForegroundColor Cyan
Write-Host ""

# Run install.sh inside WSL
Write-Host "  Starting installation inside WSL..." -ForegroundColor Green
Write-Host "  ─────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

if ($Distribution) {
    wsl -d $Distribution bash -l $wslPath
} else {
    wsl bash -l $wslPath
}

$exitCode = $LASTEXITCODE

Write-Host ""
Write-Host "  ─────────────────────────────────────" -ForegroundColor DarkGray

if ($exitCode -eq 0) {
    Write-Host ""
    Write-Host "  Installation completed successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "  To start Hermes:" -ForegroundColor White
    Write-Host "    wsl hermes chat" -ForegroundColor Cyan
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "  Installation finished with errors (exit code: $exitCode)" -ForegroundColor Yellow
    Write-Host "  Review the output above for details." -ForegroundColor Yellow
}

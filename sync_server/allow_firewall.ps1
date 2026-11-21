<#
.SYNOPSIS
    Allow inbound TCP traffic on port 8765 for Kindle KOReader local sync.
    Run this script in an elevated (Administrator) PowerShell prompt.
#>
param(
    [int]$Port = 8765,
    [string[]]$Profile = @("Private", "Domain", "Public"),
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$RuleName = "Kindle Article Sync Server (TCP $Port)"

$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Administrator privileges are required to modify Windows Defender Firewall rules. Please re-run PowerShell as Administrator."
    exit 1
}

$Existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($Remove) {
    if ($Existing) {
        Remove-NetFirewallRule -DisplayName $RuleName
        Write-Host "Removed firewall rule: $RuleName"
    }
    else {
        Write-Host "Firewall rule not found: $RuleName"
    }
    exit 0
}

if ($Existing) {
    Remove-NetFirewallRule -DisplayName $RuleName
}

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -RemoteAddress LocalSubnet `
    -Profile $Profile | Out-Null

Write-Host "Allowed inbound TCP port $Port from LocalSubnet ($($Profile -join ', ')) via rule '$RuleName'."

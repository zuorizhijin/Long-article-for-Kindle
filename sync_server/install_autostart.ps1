<#
.SYNOPSIS
    Install or uninstall Windows logon autostart for Kindle Article Sync Server.
#>
param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "KindleArticleSyncServer"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VbsPath = Join-Path $ScriptDir "start_server_hidden.vbs"
$StartupDir = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupDir "$TaskName.lnk"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed Scheduled Task: $TaskName"
    }
    if (Test-Path $ShortcutPath) {
        Remove-Item $ShortcutPath -Force
        Write-Host "Removed Startup shortcut: $ShortcutPath"
    }
    exit 0
}

if (-not (Test-Path $VbsPath)) {
    throw "Missing launcher script: $VbsPath"
}

try {
    $Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "//B //NoLogo `"$VbsPath`"" -WorkingDirectory $ScriptDir
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
    Write-Host "Installed Scheduled Task '$TaskName' for user $env:USERNAME."
}
catch {
    Write-Warning "Scheduled Task registration failed ($($_.Exception.Message)). Falling back to Startup folder shortcut."
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "wscript.exe"
    $Shortcut.Arguments = "//B //NoLogo `"$VbsPath`""
    $Shortcut.WorkingDirectory = $ScriptDir
    $Shortcut.Save()
    Write-Host "Created Startup shortcut: $ShortcutPath"
}

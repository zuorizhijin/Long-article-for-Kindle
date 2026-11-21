<#
.SYNOPSIS
    将 articlesync.koplugin 自动安装到 Kindle 的 koreader/plugins/ 目录，
    并自动检测本机局域网 IP 写入 koreader/settings/articlesync.lua。
.DESCRIPTION
    通过卷标 "Kindle" 自动识别 Kindle 盘符，将同目录下的 articlesync.koplugin
    复制到 <Kindle盘符>:\koreader\plugins\articlesync.koplugin\，
    并自动写入当前电脑的局域网 IPv4 地址，免去在 Kindle 屏幕上手动输入。
#>
param(
    [int]$Port = 8765,
    [switch]$SkipServerUrl
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginSource = Join-Path $ScriptDir "articlesync.koplugin"

if (-not (Test-Path $PluginSource)) {
    Write-Host "[错误] 未找到插件目录: $PluginSource" -ForegroundColor Red
    exit 1
}

function Find-KindleDrive {
    $vol = Get-Volume -ErrorAction SilentlyContinue | Where-Object {
        $_.FileSystemLabel -eq "Kindle" -and $_.DriveLetter
    } | Select-Object -First 1

    if ($vol) {
        return "$($vol.DriveLetter):"
    }

    $disk = Get-CimInstance Win32_LogicalDisk -ErrorAction SilentlyContinue | Where-Object {
        $_.VolumeName -eq "Kindle"
    } | Select-Object -First 1

    if ($disk) {
        return $disk.DeviceID
    }

    return $null
}

function Get-ActiveLanIPv4 {
    $excludedPrefixes = @("127.", "169.254.", "10.0.0.1", "198.18.", "198.19.")
    $excludedAdapterPattern = "vEthernet|WSL|Hyper-V|VirtualBox|VMware|Loopback|Bluetooth|TAP|TUN|ZeroTier|Tailscale|Clash|Mihomo|Sing-box"

    $defaultRoutes = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Sort-Object -Property RouteMetric

    $candidates = @()
    $adapters = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {
        $_.Status -eq "Up" -and $_.Name -notmatch $excludedAdapterPattern -and $_.InterfaceDescription -notmatch $excludedAdapterPattern
    }

    foreach ($adapter in $adapters) {
        $ips = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
        foreach ($ipObj in $ips) {
            $ip = $ipObj.IPAddress
            if (-not $ip) { continue }
            $skip = $false
            foreach ($prefix in $excludedPrefixes) {
                if ($ip -eq $prefix -or $ip.StartsWith($prefix)) {
                    $skip = $true
                    break
                }
            }
            if ($skip) { continue }

            $hasGateway = ($defaultRoutes | Where-Object { $_.InterfaceIndex -eq $adapter.ifIndex }).Count -gt 0
            $isWifi = ($adapter.Name -match "WLAN|Wi-Fi|WiFi|Wireless") -or ($adapter.InterfaceDescription -match "Wireless|Wi-Fi|WLAN|802\.11")
            $priority = 100
            if ($hasGateway) { $priority -= 50 }
            if ($isWifi) { $priority -= 20 }
            if ($ip.StartsWith("192.168.")) { $priority -= 10 }

            $candidates += [PSCustomObject]@{
                IP       = $ip
                Adapter  = $adapter.Name
                Priority = $priority
            }
        }
    }

    $best = $candidates | Sort-Object -Property Priority | Select-Object -First 1
    if ($best) {
        return $best.IP
    }
    return $null
}

function Set-KoReaderServerUrl {
    param(
        [string]$SettingsPath,
        [string]$ServerUrl
    )

    $settingsDir = Split-Path -Parent $SettingsPath
    if (-not (Test-Path $settingsDir)) {
        New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    if (Test-Path $SettingsPath) {
        $existing = [System.IO.File]::ReadAllText($SettingsPath, $utf8NoBom)
        if ($existing -match '\["server_url"\]\s*=\s*"[^"]*"') {
            $updated = [regex]::Replace($existing, '\["server_url"\]\s*=\s*"[^"]*"', "[`"server_url`"] = `"$ServerUrl`"")
        } elseif ($existing -match 'return\s*\{') {
            $updated = ([regex]'(return\s*\{)').Replace($existing, "`$1`n    [`"server_url`"] = `"$ServerUrl`",", 1)
        } else {
            $updated = "-- /mnt/us/koreader/settings/articlesync.lua`nreturn {`n    [`"server_url`"] = `"$ServerUrl`",`n}`n"
        }
        [System.IO.File]::WriteAllText($SettingsPath, $updated, $utf8NoBom)
    } else {
        $content = "-- /mnt/us/koreader/settings/articlesync.lua`nreturn {`n    [`"server_url`"] = `"$ServerUrl`",`n}`n"
        [System.IO.File]::WriteAllText($SettingsPath, $content, $utf8NoBom)
    }
}

$KindleDrive = Find-KindleDrive

if (-not $KindleDrive) {
    Write-Host "[未检测到 Kindle]" -ForegroundColor Yellow
    Write-Host "请确认："
    Write-Host "  1. 已用数据线将 Kindle 连接到电脑；"
    Write-Host "  2. 已在 Kindle 上退出 KOReader（KOReader 在前台运行时不会进入 USB 驱动器模式）；"
    Write-Host "  3. 资源管理器中能看到卷标为 'Kindle' 的磁盘。"
    exit 1
}

Write-Host "[已检测到 Kindle] 盘符: $KindleDrive" -ForegroundColor Green

$PluginsDir = Join-Path "$KindleDrive\" "koreader\plugins"
if (-not (Test-Path $PluginsDir)) {
    Write-Host "[错误] 未找到 KOReader 插件目录: $PluginsDir" -ForegroundColor Red
    Write-Host "请确认 Kindle 已正确安装 KOReader。"
    exit 1
}

$DestDir = Join-Path $PluginsDir "articlesync.koplugin"
if (Test-Path $DestDir) {
    Write-Host "[更新] 正在覆盖已有插件: $DestDir" -ForegroundColor Cyan
    Remove-Item -Path $DestDir -Recurse -Force
}

Copy-Item -Path $PluginSource -Destination $DestDir -Recurse -Force

$RequiredFiles = @("_meta.lua", "main.lua", "articlesync_http.lua")
foreach ($f in $RequiredFiles) {
    $check = Join-Path $DestDir $f
    if (-not (Test-Path $check)) {
        Write-Host "[错误] 安装校验失败，缺少文件: $check" -ForegroundColor Red
        exit 1
    }
}

Write-Host "[安装成功] 已将 articlesync.koplugin 安装到: $DestDir" -ForegroundColor Green

$DocSubDirs = @("documents\文章", "documents\已读", "documents\电子书")
foreach ($sub in $DocSubDirs) {
    $docPath = Join-Path "$KindleDrive\" $sub
    if (-not (Test-Path $docPath)) {
        New-Item -ItemType Directory -Path $docPath -Force | Out-Null
        Write-Host "[目录初始化] 已创建: $docPath" -ForegroundColor Cyan
    }
}

if (-not $SkipServerUrl) {
    $lanIp = Get-ActiveLanIPv4
    if ($lanIp) {
        $serverUrl = "http://${lanIp}:${Port}"
        $settingsFile = Join-Path "$KindleDrive\" "koreader\settings\articlesync.lua"
        Set-KoReaderServerUrl -SettingsPath $settingsFile -ServerUrl $serverUrl
        Write-Host "[配置同步] 已自动写入服务地址: $serverUrl -> $settingsFile" -ForegroundColor Green
    } else {
        Write-Host "[提示] 未能自动识别局域网 IPv4，请在 Kindle 上手动设置电脑服务地址。" -ForegroundColor Yellow
    }
}

Write-Host "请安全弹出 Kindle，然后打开 KOReader，在「工具 -> 文章同步」中即可使用。"

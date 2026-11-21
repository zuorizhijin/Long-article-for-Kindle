# articlesync.koplugin — KOReader 文章同步插件

适用于 **Kindle KT2 (KOReader 2026.7.2 + qiaomu-simpleui)** 的局域网文章同步与自动归档插件。

## 功能说明

- **未读文章同步**：连接 PC 端同步服务（`GET /api/articles?status=unread`），自动下载尚未存在于本地的 `.epub` 文章到 `/mnt/us/documents/文章/`。
- **读完自动归档**：阅读 `/mnt/us/documents/文章/` 内的文章到达最后一页（`EndOfBook` / `PageUpdate`）时，提示一键移入 `/mnt/us/documents/已读/`（连同 `.sdr` 阅读进度文件夹一起移动），并调用 `POST /api/articles/<filename>/mark-read` 通知电脑端。
- **离线队列**：如果归档时 Kindle 未连 Wi-Fi 或电脑端未开机，已读记录会保存在本地 `articlesync.lua` 设置中，下次联网或点击同步时自动补发。
- **Wi-Fi 连接自动同步**：可选开启“启用 Wi-Fi 连接自动同步”（带 60 秒冷却防抖）。

## 目录结构

```text
koreader_plugin/
├── articlesync.koplugin/
│   ├── _meta.lua         # 插件元信息
│   ├── main.lua          # 主逻辑、菜单 UI、自动归档、设置持久化
│   └── articlesync_http.lua # HTTP 客户端（LuaSocket + JSON）
├── install_to_kindle.ps1 # Windows 一键安装脚本（按卷标 Kindle 自动识别盘符）
└── README.md
```

## 安装方法（USB）

> **重要提示**：KOReader 在前台运行时，Kindle 插上电脑不会进入 USB 驱动器模式。请先在 Kindle 的 KOReader 菜单中**退出 KOReader** 回到原生书架，再连接 USB。

### 方式一：运行一键安装脚本（推荐）

在 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_to_kindle.ps1
```

脚本会自动查找卷标为 `Kindle` 的盘符，并将 `articlesync.koplugin` 复制到 `<Kindle盘符>:\koreader\plugins\articlesync.koplugin\`。

### 方式二：手动复制

将 `articlesync.koplugin` 整个文件夹复制到 Kindle 根目录下的：

```text
koreader/plugins/articlesync.koplugin/
```

安全弹出 Kindle 后，重新打开 KOReader 即可生效。

## 使用与配置

1. 确保电脑端 `sync_server` 已启动（默认端口 `8765`，且 Windows 防火墙已放行）。
2. 在 KOReader 顶部菜单打开 **工具（扳手图标）→ 文章同步**：
   - **设置电脑服务地址**：输入电脑局域网 IP 和端口，例如 `http://192.168.1.100:8765`。
   - **测试服务连接**：验证 Kindle 能否正常访问电脑端 `/health`。
   - **立即同步未读文章**：拉取未读 `.epub` 到 `/mnt/us/documents/文章/`，并补发待同步的已读状态。
   - **同步已读状态**：手动补发离线期间积累的已读回传队列。
   - **启用 Wi-Fi 连接自动同步**：开启后，每次 Kindle 连上 Wi-Fi 时自动在后台同步一次。

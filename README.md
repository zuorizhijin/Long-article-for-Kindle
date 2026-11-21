# Long Article for Kindle

不依赖亚马逊官方服务、也不依赖第三方云盘的 **Kindle 长文章本地同步方案**。  
在 Chrome 浏览器中看到博客或 X 长文章（X Articles），或者本地 Obsidian 笔记，一键打包成内嵌图片的 **EPUB3** 存入电脑本地服务，Kindle（越狱 + KOReader）连上同一 Wi-Fi 一键拉取离线阅读；读到最后一页自动移进「已读」并回传电脑归档，避免重复下载。

## 整体架构

```text
Chrome 扩展 / Obsidian 本地笔记
        │  打包正文与图片 -> EPUB3
        ▼  POST http://127.0.0.1:8765/api/upload
电脑本地同步服务 (sync_server, Python 3 零第三方依赖)
  ├── data/inbox/   (未读队列)
  ├── data/read/    (已读归档)
  └── data/state.json
        ▲  Wi-Fi 局域网同步 (GET 未读列表/下载 & POST mark-read)
        ▼
Kindle KOReader 插件 (articlesync.koplugin)
  ├── /mnt/us/documents/文章/
  └── /mnt/us/documents/已读/
```

## 目录结构

- `sync_server/`：电脑端轻量 Python 3 HTTP 同步服务、纯标准库 EPUB3 打包器、Obsidian Markdown 转换命令行工具，以及 Windows 启动/开机自启/防火墙放行脚本。
- `koreader_plugin/`：Kindle 端 KOReader 插件 `articlesync.koplugin`，以及自动识别 `Kindle` 卷标并自动写入局域网 IP 的部署脚本 `install_to_kindle.ps1`。
- `chrome_extension/`：Manifest V3 Chrome 扩展（免构建直接加载），支持 X 长文章（自动过滤普通推文/串推）、普通网页文章与本地 Obsidian 文件夹转 EPUB3。
- `工具/install_ko.py`：从电脑向 Kindle 离线安装 KOReader `.kpkg` 的辅助脚本。
- `GITHUB_SYNC.md`：本仓库的 GitHub 同步记录与后续更新发版指南。

## 快速上手（3 步）

### 1. 启动电脑端服务
1. 复制 `sync_server/config.example.json` 为 `sync_server/config.json`（按需修改 `obsidian_vault` 路径）。
2. 双击运行 `sync_server/start_server.bat` 启动服务（默认监听 `0.0.0.0:8765`）。
3. 右键以**管理员身份**运行一次 `sync_server/allow_firewall.ps1`，放行局域网入站 `8765` 端口。
4. *(可选)* 运行 `sync_server/install_autostart.ps1` 配置开机后台静默自启。

### 2. 加载 Chrome 扩展
1. 打开 `chrome://extensions/`，开启右上角 **开发者模式**。
2. 点击 **加载已解压的扩展程序**，选择 `chrome_extension/` 目录。

### 3. 安装 Kindle KOReader 插件
1. 在 Kindle 上退出 KOReader 回到原生系统，用 USB 线连接电脑。
2. 运行 `koreader_plugin/install_to_kindle.ps1`（自动识别 `Kindle` 盘符、复制插件并写入你电脑的局域网 IP）。
3. 安全弹出 Kindle，打开 KOReader → **工具 → 文章同步 → 测试服务连接**，确认连通后即可点击 **立即同步未读文章**。

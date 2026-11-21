# Long Article for Kindle

不依赖亚马逊官方服务、也不依赖第三方云盘的 **Kindle 长文章与电子书本地局域网同步方案**。  
在 Chrome 浏览器中看到博客或 X 长文章（X Articles），或本地 Obsidian 笔记（支持多选批量转换与弹窗选择/新建分类子文件夹），一键打包成内嵌图片的 **EPUB3** 存入电脑本地服务；正式电子书（EPUB/PDF/MOBI 等）也可直接拖入电脑本地目录。Kindle（越狱 + KOReader + SimpleUI）连上同一 Wi-Fi 后，在主页点一下 **「一键全部同步」** 即可按子文件夹原样离线拉取；长文章读到最后一页自动保留分类移进「已读」并回传电脑归档，电子书则长期保留不移走。

> 交互式项目展示页：下载仓库后直接用浏览器打开 [`docs/showcase.html`](docs/showcase.html) 即可体验完整同步链路与 Kindle 主页 5 键模拟。

## 整体架构

```text
Chrome 扩展（网页 / X 长文章 / Obsidian 多选批量 + 弹窗选/建分类子文件夹）
        │  打包正文与图片 -> EPUB3
        ▼  POST http://127.0.0.1:8765/api/upload (+ folder)
电脑本地同步服务 (sync_server, Python 3 零第三方依赖)
  ├── data/inbox/   (未读长文章，支持多级子文件夹)
  ├── data/read/    (已读归档，保留原分类子文件夹)
  ├── data/books/   (本地电子书直传目录，支持多级子文件夹与流式下载)
  └── data/state.json
        ▲  Wi-Fi 局域网「一键全部同步」& 归档回传
        ▼
Kindle KOReader 插件 (articlesync.koplugin)
  ├── /mnt/us/documents/文章/    (含子文件夹，读完自动归档)
  ├── /mnt/us/documents/已读/    (保留子文件夹与 .sdr 笔记)
  └── /mnt/us/documents/电子书/  (含子文件夹，长期保留不移走)
```

## 目录结构

- `sync_server/`：电脑端轻量 Python 3 HTTP 同步服务（支持文章/电子书子文件夹递归扫描、64 KiB 分块流式下载、原生文件夹选择接口 `/api/folders/pick`）、纯标准库 EPUB3 打包器、Obsidian Markdown 转换命令行工具，以及 Windows 启动/开机自启/防火墙放行脚本。
- `koreader_plugin/`：Kindle 端 KOReader 插件 `articlesync.koplugin`（支持「一键全部同步」、电子书同步、保留子文件夹归档、以及注册 6 个通用 Dispatcher 快捷动作供 SimpleUI 主页/底栏绑定），及自动识别 `Kindle` 盘符并写入局域网 IP 的部署脚本 `install_to_kindle.ps1`。
- `chrome_extension/`：Manifest V3 Chrome 扩展（免构建直接加载），支持 X 长文章、普通网页与本地 Obsidian 多选批量转 EPUB3，并支持通过电脑原生弹窗选择/新建分类子文件夹。
- `docs/showcase.html`：零依赖离线可打开的交互式项目展示文档。
- `工具/install_ko.py`：从电脑向 Kindle 离线安装 KOReader `.kpkg` 的辅助脚本。
- `GITHUB_SYNC.md`：本仓库的 GitHub 同步记录与后续更新发版指南。

## 快速上手（3 步）

### 1. 启动电脑端服务
1. 复制 `sync_server/config.example.json` 为 `sync_server/config.json`（按需修改 `obsidian_vault` 与 `books_dir` 路径）。
2. 双击运行 `sync_server/start_server.bat` 启动服务（默认监听 `0.0.0.0:8765`）。
3. 右键以**管理员身份**运行一次 `sync_server/allow_firewall.ps1`，放行局域网入站 `8765` 端口。
4. *(可选)* 运行 `sync_server/install_autostart.ps1` 配置开机后台静默自启。

### 2. 加载 Chrome 扩展
1. 打开 `chrome://extensions/`，开启右上角 **开发者模式**。
2. 点击 **加载已解压的扩展程序**，选择 `chrome_extension/` 目录。
3. 在插件中可一键发送当前网页/X 长文章，或在「本地 Obsidian」面板多选批量发送，并点击 **选择 / 新建分类文件夹** 直接在电脑原生窗口指定目标子文件夹。

### 3. 安装 Kindle KOReader 插件
1. 在 Kindle 上退出 KOReader 回到原生系统，用 USB 线连接电脑。
2. 运行 `koreader_plugin/install_to_kindle.ps1`（自动识别 `Kindle` 盘符、复制插件、创建 `文章 / 已读 / 电子书` 目录并写入你电脑的局域网 IP）。
3. 安全弹出 Kindle，打开 KOReader → **工具 → 文章同步 → 测试服务连接**，确认连通后即可点击 **一键全部同步**；也可在 SimpleUI 的 **快捷操作** 中将「文章」「电子书」「已读」「一键全部同步」「同步地址」直接摆到主页。

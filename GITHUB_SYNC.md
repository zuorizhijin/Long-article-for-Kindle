# GitHub 同步操作记录与后续更新指南

> 本文件用于记录本项目的 GitHub 仓库信息、每次发版/同步的操作历史，以及后续新增功能时的标准同步 SOP。  
> 无论是作者本人还是后续接手的 AI Agent，需要把新改动同步到 GitHub 时，**直接按本文档执行即可**。

---

## 1. 仓库元信息

| 项目 | 值 |
|---|---|
| GitHub 仓库 | `https://github.com/zuorizhijin/Long-article-for-Kindle.git` |
| 可见性 | Public（公开仓库 — 提交前必须确保无个人隐私/路径/密钥） |
| 默认分支 | `main` |
| 远程别名 | `origin` |
| 认证方式 | HTTPS + Windows Git Credential Manager (GCM) |
| 纳入版本控制的内容 | `sync_server/`（不含 `data/` 和 `config.json`）、`koreader_plugin/`、`chrome_extension/`、`工具/install_ko.py`、`README.md`、`.gitignore`、`GITHUB_SYNC.md` |
| 严禁提交的内容（由 `.gitignore` 保护） | `Kindle备份_*/`、`阅读版/`、`md转Kindle/`、`sync_server/data/`、`sync_server/config.json`、`项目进度.md`、`.obsidian/`、`.claude/`、`IMG_*.jpg`、`*.epub`、`*.azw3` |

---

## 2. 后续功能更新的标准同步 SOP

每次开发完新功能或修复 Bug，准备同步到 GitHub 时，按以下 5 步执行：

### 步骤 1：检查改动与隐私安全
在项目根目录运行：
```powershell
git status
git diff --stat
```
确认：
- 没有意外追踪到 `sync_server/data/`、`sync_server/config.json`、`项目进度.md`、`Kindle备份_*` 或任何电子书/个人路径。
- 如有新增配置项，先同步更新 `sync_server/config.example.json`（使用占位路径，不写本机真实路径）。

### 步骤 2：验证基础语法
- Python 服务：`python -m py_compile sync_server/server.py sync_server/state_store.py sync_server/epub_builder.py sync_server/md_to_epub.py`
- Chrome 扩展：`node --check chrome_extension/popup.js chrome_extension/background.js chrome_extension/content/extractor.js chrome_extension/lib/epub_builder.js chrome_extension/lib/markdown.js chrome_extension/lib/idb_store.js`

### 步骤 3：暂存并提交
使用清晰的前缀规范提交信息（`feat:` 新功能 / `fix:` 修复 / `docs:` 文档 / `refactor:` 重构）：
```powershell
git add .
git commit -m "feat: <简明描述本次改动>"
```

### 步骤 4：推送到 GitHub（以及里程碑打标签）
常规推送：
```powershell
git push origin main
```
若本次更新达到一个可发布的新版本（例如 `v1.1.0` 或修复版 `v1.0.1`），同时打轻量/附注标签并推送：
```powershell
git tag -a v1.1.0 -m "v1.1.0: <版本核心亮点>"
git push origin v1.1.0
```

### 步骤 5：更新下方「同步历史记录表」
在下方表格追加一行本次同步的日期、版本/Tag、Commit 短哈希和改动摘要，顺手提交或合并在本次发布提交中。

---

## 3. 同步历史记录表（Sync Log）

| 日期 | 版本 / Tag | 分支 | Commit | 同步内容摘要 | 验证状态 |
|---|---|---|---|---|---|
| 2026-11-22 | `v1.0.0` | `main` | `65b84fb` | 第一版完整跑通并首次同步：电脑本地 HTTP 服务（`sync_server/`）、Kindle KOReader 插件（`koreader_plugin/`，含 KT2 `ui/uimanager` 等真机修复）、Chrome MV3 扩展（`chrome_extension/`）、KOReader 离线安装脚本（`工具/install_ko.py`）及公开仓库脱敏配置 | 已通过真机端到端同步测试 |
| 2026-11-22 | `v1.1.0` | `main` | `a6611bd` | 新增本地电子书局域网流式同步（`data/books/`）、Chrome 本地 Obsidian 多选批量同步、原生弹窗选择/新建分类子文件夹（`POST /api/folders/pick`）及全链路多级子文件夹同步与保留分类归档、Kindle 主页 5 键与「一键全部同步 / 同步地址」通用 Dispatcher 快捷动作，并新增交互式项目展示页 [`docs/showcase.html`](docs/showcase.html) | 已通过全链路自动化校验与 Kindle 真机实测 |

# Kindle Article Sync (Chrome Extension)

Manifest V3 Chrome 扩展，无需构建工具（无 npm 依赖），直接加载即可使用。

## 安装方法

1. 打开 Chrome 浏览器，访问 `chrome://extensions/`
2. 打开右上角 **开发者模式**
3. 点击 **加载已解压的扩展程序**，选择本目录 `chrome_extension`

## 功能说明

1. **网页 / X 长文章**
   - 自动识别普通网页博客（Readability 风格正文提取）和 X（`x.com` / `twitter.com`）长文章（X Articles）。
   - **X 平台限制**：仅支持 X 长文章；若当前为普通推文或推文串，会明确提示不支持并禁止发送。
   - 自动抓取文中图片并嵌入 EPUB `OEBPS/images/`；视频会替换为封面图 + 原文链接。
2. **本地 Obsidian MD**
   - 通过 File System Access API 授权你的本地 Obsidian 知识库文件夹（权限句柄持久化在 IndexedDB）。
   - 支持搜索 `.md` 笔记、解析 `![[image]]` 和 `![](image)` 本地图片嵌入并打包为 EPUB。
   - 若弹窗在系统文件夹选择框打开时自动关闭，可点击「独立标签页」在标签页中完成授权与转换。
3. **同步服务集成**
   - 默认连接 `http://127.0.0.1:8765`（右上角齿轮可修改），顶部实时显示 `/health` 状态与未读数量。
   - 点击「生成 EPUB 并发送到电脑」通过 `POST /api/upload` 上传；若服务离线，可点击「下载 EPUB」保存到本地。

local ConfirmBox = require("ui/widget/confirmbox")
local DataStorage = require("datastorage")
local Dispatcher = require("dispatcher")
local InfoMessage = require("ui/widget/infomessage")
local InputDialog = require("ui/widget/inputdialog")
local LuaSettings = require("luasettings")
local NetworkMgr = require("ui/network/manager")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local lfs = require("libs/libkoreader-lfs")
local logger = require("logger")
local _ = require("gettext")

local HttpClient = require("articlesync_http")

local UNREAD_DIR = "/mnt/us/documents/文章/"
local READ_DIR = "/mnt/us/documents/已读/"
local BOOKS_DIR = "/mnt/us/documents/电子书/"
local DEFAULT_SERVER_URL = "http://192.168.1.100:8765"
local AUTO_SYNC_COOLDOWN = 60

local ArticleSync = WidgetContainer:extend{
    name = "articlesync",
    is_doc_only = false,
}

local function stripTrailingSlash(path)
    return (tostring(path or ""):gsub("/+$", ""))
end

local function ensureDir(path)
    local clean = stripTrailingSlash(path)
    if clean == "" then
        return false
    end
    if lfs.attributes(clean, "mode") == "directory" then
        return true
    end
    local parent = clean:match("^(.+)/[^/]+$")
    if parent and parent ~= "" and lfs.attributes(parent, "mode") ~= "directory" then
        ensureDir(parent)
    end
    local ok, err = lfs.mkdir(clean)
    if not ok and lfs.attributes(clean, "mode") ~= "directory" then
        logger.warn("ArticleSync: failed to mkdir", clean, err)
        return false
    end
    return true
end

local function fileExists(path)
    return lfs.attributes(path, "mode") == "file"
end

local function dirExists(path)
    return lfs.attributes(path, "mode") == "directory"
end

local function removeRecursive(path)
    local mode = lfs.attributes(path, "mode")
    if not mode then
        return true
    end
    if mode == "file" or mode == "link" then
        return os.remove(path)
    end
    if mode == "directory" then
        for entry in lfs.dir(path) do
            if entry ~= "." and entry ~= ".." then
                removeRecursive(path .. "/" .. entry)
            end
        end
        return lfs.rmdir(path)
    end
    return false
end

local function getBasename(filepath)
    return tostring(filepath or ""):match("([^/]+)$")
end

local function getSidecarDir(filepath)
    return (tostring(filepath or ""):gsub("%.([^./]+)$", ".sdr"))
end

local function isInUnreadFolder(filepath)
    if type(filepath) ~= "string" then
        return false
    end
    return filepath:sub(1, #UNREAD_DIR) == UNREAD_DIR
end

local function isSafeRelPath(rel_path)
    if type(rel_path) ~= "string" or rel_path == "" then
        return false
    end
    if rel_path:sub(1, 1) == "/" or rel_path:find("\\", 1, true) then
        return false
    end
    if rel_path:match("//") or rel_path:sub(-1) == "/" then
        return false
    end
    for seg in rel_path:gmatch("[^/]+") do
        if seg == "." or seg == ".." then
            return false
        end
    end
    return true
end

local function ensureParentDir(filepath)
    local parent = tostring(filepath or ""):match("^(.+)/[^/]+$")
    if parent and parent ~= "" then
        return ensureDir(parent)
    end
    return true
end

local function refreshFileManager()
    local FileManager = package.loaded["apps/filemanager/filemanager"]
    if FileManager and FileManager.instance then
        pcall(function()
            FileManager.instance:onRefresh()
        end)
    end
end

local function showLoadingMessage(text)
    local msg = InfoMessage:new{
        text = text,
    }
    UIManager:show(msg)
    UIManager:forceRePaint()
    return msg
end

function ArticleSync:init()
    self.settings = LuaSettings:open(DataStorage:getSettingsDir() .. "/articlesync.lua")
    self._last_auto_sync_ts = 0
    self._is_syncing = false
    self._is_syncing_books = false
    self._archived_or_prompted = {}

    ensureDir(UNREAD_DIR)
    ensureDir(READ_DIR)
    ensureDir(BOOKS_DIR)

    pcall(function()
        self:onDispatcherRegisterActions()
    end)

    self.ui.menu:registerToMainMenu(self)
end

function ArticleSync:onDispatcherRegisterActions()
    Dispatcher:registerAction("articlesync_sync_all", {
        category = "none",
        event = "ArticleSyncSyncAll",
        title = _("一键全部同步"),
        general = true,
    })
    Dispatcher:registerAction("articlesync_sync_articles", {
        category = "none",
        event = "ArticleSyncSyncArticles",
        title = _("同步未读文章"),
        general = true,
    })
    Dispatcher:registerAction("articlesync_sync_books", {
        category = "none",
        event = "ArticleSyncSyncBooks",
        title = _("同步电子书"),
        general = true,
    })
    Dispatcher:registerAction("articlesync_open_articles", {
        category = "none",
        event = "ArticleSyncOpenArticles",
        title = _("打开【文章】文件夹"),
        general = true,
    })
    Dispatcher:registerAction("articlesync_open_books", {
        category = "none",
        event = "ArticleSyncOpenBooks",
        title = _("打开【电子书】文件夹"),
        general = true,
    })
    Dispatcher:registerAction("articlesync_set_server_url", {
        category = "none",
        event = "ArticleSyncSetServerUrl",
        title = _("设置同步地址"),
        general = true,
    })
end

function ArticleSync:onArticleSyncSetServerUrl()
    self:showServerUrlDialog()
    return true
end

function ArticleSync:onArticleSyncSyncAll()
    self:syncAll(false)
    return true
end

function ArticleSync:onArticleSyncSyncArticles()
    self:syncArticles(false)
    return true
end

function ArticleSync:onArticleSyncOpenArticles()
    self:openFolder(UNREAD_DIR, _("文章"))
    return true
end

function ArticleSync:onArticleSyncOpenBooks()
    self:openFolder(BOOKS_DIR, _("电子书"))
    return true
end

function ArticleSync:onArticleSyncSyncBooks()
    self:syncBooks(false)
    return true
end

local function switchFmToFolder(target_fm, clean_path)
    if not target_fm or not target_fm.file_chooser then
        return false
    end
    target_fm._sui_show_folder_pending = true
    target_fm._navbar_suppress_path_change = true
    target_fm.file_chooser:changeToPath(clean_path)
    target_fm._navbar_suppress_path_change = nil
    target_fm._sui_show_folder_pending = nil
    if target_fm.updateTitleBarPath then
        pcall(function()
            target_fm:updateTitleBarPath(clean_path, true)
        end)
    end
    return true
end

function ArticleSync:openFolder(path, _label)
    ensureDir(path)
    local clean_path = stripTrailingSlash(path)
    local FileManager = package.loaded["apps/filemanager/filemanager"]

    if self.ui and self.ui.file_chooser then
        switchFmToFolder(self.ui, clean_path)
        return
    end

    if self.ui and self.ui.onClose and not self.ui.file_chooser then
        local ui = self.ui
        ui:onClose()
        UIManager:nextTick(function()
            if ui.showFileManager then
                ui:showFileManager(clean_path)
            else
                require("apps/filemanager/filemanager"):showFiles(clean_path)
            end
        end)
        return
    end

    if FileManager and FileManager.instance and FileManager.instance.file_chooser then
        switchFmToFolder(FileManager.instance, clean_path)
        return
    end

    require("apps/filemanager/filemanager"):showFiles(clean_path)
end

function ArticleSync:getServerUrl()
    return self.settings:readSetting("server_url", DEFAULT_SERVER_URL)
end

function ArticleSync:setServerUrl(url)
    local clean = tostring(url or ""):gsub("^%s+", ""):gsub("%s+$", ""):gsub("/+$", "")
    self.settings:saveSetting("server_url", clean)
    self.settings:flush()
end

function ArticleSync:isAutoSyncEnabled()
    return self.settings:readSetting("auto_sync_wifi", false) == true
end

function ArticleSync:toggleAutoSync()
    local next_val = not self:isAutoSyncEnabled()
    self.settings:saveSetting("auto_sync_wifi", next_val)
    self.settings:flush()
end

function ArticleSync:getPendingAcks()
    local list = self.settings:readSetting("pending_acks", {})
    if type(list) ~= "table" then
        return {}
    end
    return list
end

function ArticleSync:savePendingAcks(list)
    self.settings:saveSetting("pending_acks", list)
    self.settings:flush()
end

function ArticleSync:addPendingAck(filename)
    local list = self:getPendingAcks()
    for _, existing in ipairs(list) do
        if existing == filename then
            return
        end
    end
    table.insert(list, filename)
    self:savePendingAcks(list)
end

function ArticleSync:addToMainMenu(menu_items)
    menu_items.articlesync = {
        text = _("文章同步"),
        sorting_hint = "tools",
        sub_item_table = {
            {
                text = _("一键全部同步"),
                callback = function()
                    self:syncAll(false)
                end,
            },
            {
                text = _("立即同步未读文章"),
                callback = function()
                    self:syncArticles(false)
                end,
            },
            {
                text = _("立即同步电子书"),
                callback = function()
                    self:syncBooks(false)
                end,
            },
            {
                text = _("打开【文章】文件夹"),
                callback = function()
                    self:openFolder(UNREAD_DIR, _("文章"))
                end,
            },
            {
                text = _("打开【电子书】文件夹"),
                callback = function()
                    self:openFolder(BOOKS_DIR, _("电子书"))
                end,
            },
            {
                text = _("同步已读状态"),
                keep_menu_open = true,
                callback = function(touchmenu_instance)
                    self:flushPendingAcksManual()
                    if touchmenu_instance then
                        touchmenu_instance:updateItems()
                    end
                end,
            },
            {
                text_func = function()
                    return _("服务地址: ") .. self:getServerUrl()
                end,
                callback = function()
                    self:showServerUrlDialog()
                end,
            },
            {
                text = _("测试服务连接"),
                keep_menu_open = true,
                callback = function()
                    self:testConnection()
                end,
            },
            {
                text = _("启用 Wi-Fi 连接自动同步"),
                checked_func = function()
                    return self:isAutoSyncEnabled()
                end,
                callback = function()
                    self:toggleAutoSync()
                end,
            },
        },
    }
end

function ArticleSync:showServerUrlDialog()
    local dlg
    dlg = InputDialog:new{
        title = _("电脑服务地址"),
        input = self:getServerUrl(),
        input_hint = DEFAULT_SERVER_URL,
        allow_newline = false,
        buttons = {
            {
                {
                    text = _("取消"),
                    id = "close",
                    callback = function()
                        UIManager:close(dlg)
                    end,
                },
                {
                    text = _("保存"),
                    is_enter_default = true,
                    callback = function()
                        local val = dlg:getInputText()
                        if not val or not val:match("^https?://.+") then
                            UIManager:show(InfoMessage:new{
                                text = _("地址格式无效，请以 http:// 或 https:// 开头。"),
                            })
                            return
                        end
                        self:setServerUrl(val)
                        UIManager:close(dlg)
                        UIManager:show(InfoMessage:new{
                            text = string.format(_("服务地址已保存：\n%s"), self:getServerUrl()),
                            timeout = 2,
                        })
                    end,
                },
            },
        },
    }
    UIManager:show(dlg)
    dlg:onShowKeyboard()
end

function ArticleSync:testConnection()
    NetworkMgr:runWhenConnected(function()
        local url = self:getServerUrl()
        local loading = showLoadingMessage(string.format(_("正在连接电脑服务...\n%s"), url))
        local ok, data_or_err = HttpClient.checkHealth(url)
        UIManager:close(loading)
        if ok then
            local unread = tonumber(data_or_err.unread_count) or 0
            local msg = string.format(_("连接成功！\n服务：%s\n电脑端未读文章数：%d"), url, unread)
            if data_or_err.books_count ~= nil then
                msg = msg .. string.format(_("\n电脑端电子书数：%d"), tonumber(data_or_err.books_count) or 0)
            end
            UIManager:show(InfoMessage:new{
                text = msg,
                timeout = 3,
            })
        else
            UIManager:show(InfoMessage:new{
                text = string.format(_("连接失败：\n%s\n%s"), url, tostring(data_or_err)),
            })
        end
    end)
end

function ArticleSync:flushPendingAcks()
    local list = self:getPendingAcks()
    if #list == 0 then
        return 0, 0
    end
    local server_url = self:getServerUrl()
    local remaining = {}
    local sent_count = 0

    for _, filename in ipairs(list) do
        local ok, err = HttpClient.markRead(server_url, filename)
        if ok then
            sent_count = sent_count + 1
        else
            logger.warn("ArticleSync: markRead failed for", filename, err)
            table.insert(remaining, filename)
        end
    end

    self:savePendingAcks(remaining)
    return sent_count, #remaining
end

function ArticleSync:flushPendingAcksManual()
    local list = self:getPendingAcks()
    if #list == 0 then
        UIManager:show(InfoMessage:new{
            text = _("没有待同步的已读记录。"),
            timeout = 2,
        })
        return
    end
    NetworkMgr:runWhenConnected(function()
        local loading = showLoadingMessage(string.format(_("正在同步 %d 条已读记录..."), #list))
        local sent, remain = self:flushPendingAcks()
        UIManager:close(loading)
        UIManager:show(InfoMessage:new{
            text = string.format(_("已读状态同步完成：成功 %d 条，失败 %d 条。"), sent, remain),
            timeout = 3,
        })
    end)
end

function ArticleSync:_syncArticlesInternal()
    ensureDir(UNREAD_DIR)
    ensureDir(READ_DIR)

    local server_url = self:getServerUrl()
    local ack_sent, ack_remain = self:flushPendingAcks()
    local ok_list, articles_or_err = HttpClient.fetchUnreadList(server_url)

    if not ok_list then
        return {
            ok = false,
            err = tostring(articles_or_err),
            ack_sent = ack_sent,
            ack_remain = ack_remain,
            downloaded = 0,
            skipped = 0,
            failed = 0,
        }
    end

    local downloaded = 0
    local skipped = 0
    local failed = 0

    for _, item in ipairs(articles_or_err) do
        local rel_path = type(item) == "table" and item.filename or nil
        if isSafeRelPath(rel_path) then
            local unread_path = UNREAD_DIR .. rel_path
            local read_path = READ_DIR .. rel_path

            if fileExists(unread_path) or fileExists(read_path) then
                skipped = skipped + 1
            else
                ensureParentDir(unread_path)
                local ok_dl, err_dl = HttpClient.downloadArticle(server_url, rel_path, unread_path)
                if ok_dl then
                    downloaded = downloaded + 1
                else
                    logger.warn("ArticleSync: download failed for", rel_path, err_dl)
                    failed = failed + 1
                end
            end
        elseif rel_path ~= nil then
            logger.warn("ArticleSync: skipped unsafe article path:", tostring(rel_path))
            failed = failed + 1
        end
    end

    return {
        ok = true,
        ack_sent = ack_sent,
        ack_remain = ack_remain,
        downloaded = downloaded,
        skipped = skipped,
        failed = failed,
    }
end

function ArticleSync:_syncBooksInternal()
    ensureDir(BOOKS_DIR)

    local server_url = self:getServerUrl()
    local ok_list, books_or_err = HttpClient.listBooks(server_url)
    if not ok_list then
        return {
            ok = false,
            err = tostring(books_or_err),
            downloaded = 0,
            skipped = 0,
            failed = 0,
        }
    end

    local downloaded = 0
    local skipped = 0
    local failed = 0

    for _, item in ipairs(books_or_err) do
        local rel_path = type(item) == "table" and item.filename or nil
        if isSafeRelPath(rel_path) then
            local dest_path = BOOKS_DIR .. rel_path
            local remote_size = tonumber(item.size)
            local local_size = fileExists(dest_path) and lfs.attributes(dest_path, "size") or nil

            if local_size ~= nil and (not remote_size or local_size == remote_size) then
                skipped = skipped + 1
            else
                ensureParentDir(dest_path)
                local ok_dl, err_dl = HttpClient.downloadBook(server_url, rel_path, dest_path)
                if ok_dl then
                    downloaded = downloaded + 1
                else
                    logger.warn("ArticleSync: downloadBook failed for", rel_path, err_dl)
                    failed = failed + 1
                end
            end
        elseif rel_path ~= nil then
            logger.warn("ArticleSync: skipped unsafe book path:", tostring(rel_path))
            failed = failed + 1
        end
    end

    return {
        ok = true,
        downloaded = downloaded,
        skipped = skipped,
        failed = failed,
    }
end

function ArticleSync:_doSyncArticles(quiet)
    if self._is_syncing then
        return
    end

    self._is_syncing = true
    local server_url = self:getServerUrl()
    local loading = nil
    if not quiet then
        loading = showLoadingMessage(string.format(_("正在从电脑同步文章...\n%s"), server_url))
    end

    local res = self:_syncArticlesInternal()
    self._is_syncing = false
    if loading then
        UIManager:close(loading)
    end

    if not res.ok then
        if not quiet then
            UIManager:show(InfoMessage:new{
                text = string.format(_("获取文章列表失败：\n%s\n%s"), server_url, res.err),
            })
        end
        return
    end

    if res.downloaded > 0 then
        refreshFileManager()
    end

    if not quiet or res.downloaded > 0 or res.ack_sent > 0 then
        local msg = string.format(
            _("同步完成：新下载 %d 篇，跳过 %d 篇，失败 %d 篇。"),
            res.downloaded, res.skipped, res.failed
        )
        if res.ack_sent > 0 or res.ack_remain > 0 then
            msg = msg .. string.format(_("\n已读回传：成功 %d 条，待重试 %d 条。"), res.ack_sent, res.ack_remain)
        end
        UIManager:show(InfoMessage:new{
            text = msg,
            timeout = 4,
        })
    end
end

function ArticleSync:syncArticles(quiet)
    if quiet then
        if not NetworkMgr:isConnected() then
            return
        end
        self:_doSyncArticles(true)
    else
        NetworkMgr:runWhenConnected(function()
            self:_doSyncArticles(false)
        end)
    end
end

function ArticleSync:_doSyncBooks(quiet)
    if self._is_syncing_books then
        return
    end

    self._is_syncing_books = true
    local server_url = self:getServerUrl()
    local loading = nil
    if not quiet then
        loading = showLoadingMessage(string.format(_("正在从电脑同步电子书...\n%s"), server_url))
    end

    local res = self:_syncBooksInternal()
    self._is_syncing_books = false
    if loading then
        UIManager:close(loading)
    end

    if not res.ok then
        if not quiet then
            UIManager:show(InfoMessage:new{
                text = string.format(_("获取电子书列表失败：\n%s\n%s"), server_url, res.err),
            })
        end
        return
    end

    if res.downloaded > 0 then
        refreshFileManager()
    end

    if not quiet or res.downloaded > 0 then
        UIManager:show(InfoMessage:new{
            text = string.format(
                _("电子书同步完成：新增 %d 本，跳过 %d 本，失败 %d 本。"),
                res.downloaded, res.skipped, res.failed
            ),
            timeout = 4,
        })
    end
end

function ArticleSync:syncBooks(quiet)
    if quiet then
        if not NetworkMgr:isConnected() then
            return
        end
        self:_doSyncBooks(true)
    else
        NetworkMgr:runWhenConnected(function()
            self:_doSyncBooks(false)
        end)
    end
end

function ArticleSync:_doSyncAll(quiet)
    if self._is_syncing or self._is_syncing_books then
        return
    end

    self._is_syncing = true
    self._is_syncing_books = true

    local server_url = self:getServerUrl()
    local loading = nil
    if not quiet then
        loading = showLoadingMessage(string.format(_("正在一键同步文章与电子书...\n%s"), server_url))
    end

    local art_res = self:_syncArticlesInternal()
    local book_res = self:_syncBooksInternal()

    self._is_syncing = false
    self._is_syncing_books = false
    if loading then
        UIManager:close(loading)
    end

    local total_downloaded = (art_res.downloaded or 0) + (book_res.downloaded or 0)
    if total_downloaded > 0 then
        refreshFileManager()
    end

    if not quiet or total_downloaded > 0 or (art_res.ack_sent or 0) > 0 then
        local lines = { _("一键同步完成：") }
        if art_res.ok then
            local line = string.format(_("文章：新增 %d 篇（跳过 %d 篇）"), art_res.downloaded, art_res.skipped)
            if art_res.failed > 0 then
                line = line .. string.format(_("，失败 %d 篇"), art_res.failed)
            end
            table.insert(lines, line)
        else
            table.insert(lines, string.format(_("文章：同步失败（%s）"), art_res.err))
        end

        if book_res.ok then
            local line = string.format(_("电子书：新增 %d 本（跳过 %d 本）"), book_res.downloaded, book_res.skipped)
            if book_res.failed > 0 then
                line = line .. string.format(_("，失败 %d 本"), book_res.failed)
            end
            table.insert(lines, line)
        else
            table.insert(lines, string.format(_("电子书：同步失败（%s）"), book_res.err))
        end

        if (art_res.ack_sent or 0) > 0 or (art_res.ack_remain or 0) > 0 then
            table.insert(lines, string.format(_("已读回传：成功 %d 条，待重试 %d 条"), art_res.ack_sent, art_res.ack_remain))
        end

        UIManager:show(InfoMessage:new{
            text = table.concat(lines, "\n"),
            timeout = 4,
        })
    end
end

function ArticleSync:syncAll(quiet)
    if quiet then
        if not NetworkMgr:isConnected() then
            return
        end
        self:_doSyncAll(true)
    else
        NetworkMgr:runWhenConnected(function()
            self:_doSyncAll(false)
        end)
    end
end

function ArticleSync:onNetworkConnected()
    if not self:isAutoSyncEnabled() then
        return
    end
    local now = os.time()
    if now - self._last_auto_sync_ts < AUTO_SYNC_COOLDOWN then
        return
    end
    self._last_auto_sync_ts = now
    UIManager:nextTick(function()
        self:syncAll(true)
    end)
end

function ArticleSync:archiveArticle(filepath)
    ensureDir(UNREAD_DIR)
    ensureDir(READ_DIR)

    local rel_path = isInUnreadFolder(filepath) and filepath:sub(#UNREAD_DIR + 1) or getBasename(filepath)
    if not isSafeRelPath(rel_path) then
        return nil
    end

    local dest_path = READ_DIR .. rel_path
    if fileExists(filepath) then
        ensureParentDir(dest_path)
        if fileExists(dest_path) then
            os.remove(dest_path)
        end
        local ok_mv, err_mv = os.rename(filepath, dest_path)
        if not ok_mv then
            return string.format(_("归档失败：%s"), tostring(err_mv))
        end
    end

    local src_sdr = getSidecarDir(filepath)
    if dirExists(src_sdr) then
        local dest_sdr = getSidecarDir(dest_path)
        ensureParentDir(dest_sdr)
        if dirExists(dest_sdr) then
            removeRecursive(dest_sdr)
        end
        os.rename(src_sdr, dest_sdr)
    end

    pcall(function()
        require("readhistory"):updateItem(filepath, dest_path)
        require("readcollection"):updateItem(filepath, dest_path)
    end)

    if NetworkMgr:isConnected() then
        local ok_ack, err_ack = HttpClient.markRead(self:getServerUrl(), rel_path)
        if ok_ack then
            return _("已移入「已读」并通知电脑端。")
        else
            logger.warn("ArticleSync: markRead offline/error:", err_ack)
            self:addPendingAck(rel_path)
            return _("已移入「已读」（电脑端未响应，下次同步将自动回传状态）。")
        end
    else
        self:addPendingAck(rel_path)
        return _("已移入「已读」（当前离线，下次联网同步时将回传状态）。")
    end
end

function ArticleSync:promptArchiveIfNeeded(filepath)
    if not isInUnreadFolder(filepath) then
        return
    end
    if self._archived_or_prompted[filepath] then
        return
    end

    local filename = getBasename(filepath) or ""
    local display_name = filename:gsub("%.epub$", "")

    UIManager:show(ConfirmBox:new{
        text = string.format(_("已读完《%s》，是否将其移入「已读」文件夹？"), display_name),
        ok_text = _("移入已读"),
        cancel_text = _("暂不"),
        ok_callback = function()
            self._archived_or_prompted[filepath] = true
            local ui = self.ui
            if ui and ui.onClose then
                ui:onClose()
            end
            UIManager:nextTick(function()
                local ack_msg = self:archiveArticle(filepath)
                if ui and ui.showFileManager then
                    ui:showFileManager(UNREAD_DIR)
                else
                    require("apps/filemanager/filemanager"):showFiles(UNREAD_DIR)
                end
                if ack_msg then
                    UIManager:show(InfoMessage:new{
                        text = ack_msg,
                        timeout = 3,
                    })
                end
            end)
        end,
    })
end

function ArticleSync:onEndOfBook()
    if not self.ui or not self.ui.document or not self.ui.document.file then
        return
    end
    self:promptArchiveIfNeeded(self.ui.document.file)
end

return ArticleSync

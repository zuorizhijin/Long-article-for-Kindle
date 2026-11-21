local ConfirmBox = require("ui/widget/confirmbox")
local DataStorage = require("datastorage")
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
    self._archived_or_prompted = {}

    ensureDir(UNREAD_DIR)
    ensureDir(READ_DIR)

    self.ui.menu:registerToMainMenu(self)
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
                text = _("立即同步未读文章"),
                callback = function()
                    self:syncAll(false)
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
            UIManager:show(InfoMessage:new{
                text = string.format(_("连接成功！\n服务：%s\n电脑端未读文章数：%d"), url, unread),
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

function ArticleSync:_doSyncAll(quiet)
    if self._is_syncing then
        return
    end

    self._is_syncing = true
    ensureDir(UNREAD_DIR)
    ensureDir(READ_DIR)

    local server_url = self:getServerUrl()
    local loading = nil
    if not quiet then
        loading = showLoadingMessage(string.format(_("正在从电脑同步文章...\n%s"), server_url))
    end

    local ack_sent, ack_remain = self:flushPendingAcks()
    local ok_list, articles_or_err = HttpClient.fetchUnreadList(server_url)

    if not ok_list then
        self._is_syncing = false
        if loading then
            UIManager:close(loading)
        end
        if not quiet then
            UIManager:show(InfoMessage:new{
                text = string.format(_("获取文章列表失败：\n%s\n%s"), server_url, tostring(articles_or_err)),
            })
        end
        return
    end

    local downloaded = 0
    local skipped = 0
    local failed = 0

    for _, item in ipairs(articles_or_err) do
        local filename = type(item) == "table" and item.filename or nil
        if type(filename) == "string" and filename ~= "" then
            local safe_name = getBasename(filename)
            local unread_path = UNREAD_DIR .. safe_name
            local read_path = READ_DIR .. safe_name

            if fileExists(unread_path) or fileExists(read_path) then
                skipped = skipped + 1
            else
                local ok_dl, err_dl = HttpClient.downloadArticle(server_url, filename, unread_path)
                if ok_dl then
                    downloaded = downloaded + 1
                else
                    logger.warn("ArticleSync: download failed for", filename, err_dl)
                    failed = failed + 1
                end
            end
        end
    end

    self._is_syncing = false
    if loading then
        UIManager:close(loading)
    end

    if downloaded > 0 then
        refreshFileManager()
    end

    if not quiet or downloaded > 0 or ack_sent > 0 then
        local msg = string.format(
            _("同步完成：新下载 %d 篇，跳过 %d 篇，失败 %d 篇。"),
            downloaded, skipped, failed
        )
        if ack_sent > 0 or ack_remain > 0 then
            msg = msg .. string.format(_("\n已读回传：成功 %d 条，待重试 %d 条。"), ack_sent, ack_remain)
        end
        UIManager:show(InfoMessage:new{
            text = msg,
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

    local filename = getBasename(filepath)
    if not filename or filename == "" then
        return nil
    end

    local dest_path = READ_DIR .. filename
    if fileExists(filepath) then
        if fileExists(dest_path) then
            os.remove(dest_path)
        end
        local ok_mv, err_mv = os.rename(filepath, dest_path)
        if not ok_mv then
            return string.format(_("归档失败：%s"), tostring(err_mv))
        end
    end

    local src_sdr = getSidecarDir(filepath)
    local sdr_name = getBasename(src_sdr)
    if sdr_name and dirExists(src_sdr) then
        local dest_sdr = READ_DIR .. sdr_name
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
        local ok_ack, err_ack = HttpClient.markRead(self:getServerUrl(), filename)
        if ok_ack then
            return _("已移入「已读」并通知电脑端。")
        else
            logger.warn("ArticleSync: markRead offline/error:", err_ack)
            self:addPendingAck(filename)
            return _("已移入「已读」（电脑端未响应，下次同步将自动回传状态）。")
        end
    else
        self:addPendingAck(filename)
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

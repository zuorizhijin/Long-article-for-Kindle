local http = require("socket.http")
local ltn12 = require("ltn12")
local socketutil = require("socketutil")
local json = require("json")
local logger = require("logger")

local HttpClient = {}

local CONNECT_TIMEOUT = 5
local READ_TIMEOUT = 20
local BOOK_READ_TIMEOUT = 120

function HttpClient.urlEncode(str)
    if not str then
        return ""
    end
    return (tostring(str):gsub("([^%w%-%_%.%~])", function(c)
        return string.format("%%%02X", string.byte(c))
    end))
end

function HttpClient.encodePathSegments(rel_path)
    if not rel_path then
        return ""
    end
    local parts = {}
    for seg in tostring(rel_path):gmatch("[^/]+") do
        table.insert(parts, HttpClient.urlEncode(seg))
    end
    return table.concat(parts, "/")
end

local function normalizeBaseUrl(base_url)
    return tostring(base_url or ""):gsub("/+$", "")
end

local function closeHandleIfOpen(file_handle)
    if file_handle and io.type(file_handle) == "file" then
        pcall(function()
            file_handle:close()
        end)
    end
end

local function requestJson(method, url, body_tbl)
    local response_chunks = {}
    local req_body = nil
    local headers = {
        ["Accept"] = "application/json",
    }

    if body_tbl ~= nil then
        local ok_enc, encoded = pcall(json.encode, body_tbl)
        if not ok_enc then
            return false, "JSON encode error"
        end
        req_body = encoded
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = tostring(#req_body)
    elseif method == "POST" then
        req_body = "{}"
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = "2"
    end

    socketutil:set_timeout(CONNECT_TIMEOUT, READ_TIMEOUT)
    local ok, res, code, _resp_headers, status_line = pcall(function()
        return http.request({
            url = url,
            method = method,
            headers = headers,
            source = req_body and ltn12.source.string(req_body) or nil,
            sink = socketutil.table_sink(response_chunks),
        })
    end)
    socketutil:reset_timeout()

    if not ok then
        logger.warn("ArticleSync: http.request error:", res)
        return false, tostring(res)
    end

    if not res then
        return false, tostring(code or "connection failed")
    end

    local status_code = tonumber(code) or 0
    local raw_body = table.concat(response_chunks)
    if status_code < 200 or status_code >= 300 then
        return false, string.format("HTTP %d (%s)", status_code, tostring(status_line or raw_body))
    end

    if raw_body == "" then
        return true, {}
    end

    local ok_dec, data = pcall(json.decode, raw_body)
    if not ok_dec or type(data) ~= "table" then
        return false, "Invalid JSON response"
    end

    return true, data
end

local function downloadToFile(url, dest_path, read_timeout)
    local part_path = dest_path .. ".part"

    os.remove(part_path)
    local file_handle, open_err = io.open(part_path, "wb")
    if not file_handle then
        return false, "Cannot open file for writing: " .. tostring(open_err)
    end

    socketutil:set_timeout(CONNECT_TIMEOUT, read_timeout or READ_TIMEOUT)
    local ok, res, code, _resp_headers, status_line = pcall(function()
        return http.request({
            url = url,
            method = "GET",
            sink = socketutil.file_sink(file_handle),
        })
    end)
    socketutil:reset_timeout()
    closeHandleIfOpen(file_handle)

    if not ok then
        os.remove(part_path)
        return false, tostring(res)
    end

    if not res then
        os.remove(part_path)
        return false, tostring(code or "download failed")
    end

    local status_code = tonumber(code) or 0
    if status_code < 200 or status_code >= 300 then
        os.remove(part_path)
        return false, string.format("HTTP %d (%s)", status_code, tostring(status_line or ""))
    end

    local check_f = io.open(part_path, "rb")
    if not check_f then
        os.remove(part_path)
        return false, "Downloaded file missing"
    end
    local size = check_f:seek("end") or 0
    check_f:close()
    if size == 0 then
        os.remove(part_path)
        return false, "Downloaded file is empty"
    end

    os.remove(dest_path)
    local renamed, rename_err = os.rename(part_path, dest_path)
    if not renamed then
        os.remove(part_path)
        return false, "Rename failed: " .. tostring(rename_err)
    end

    return true, size
end

function HttpClient.checkHealth(base_url)
    local url = normalizeBaseUrl(base_url) .. "/health"
    local ok, data = requestJson("GET", url, nil)
    if not ok then
        return false, data
    end
    if not data.ok then
        return false, "Server health check returned ok=false"
    end
    return true, data
end

function HttpClient.fetchUnreadList(base_url)
    local url = normalizeBaseUrl(base_url) .. "/api/articles?status=unread"
    local ok, data = requestJson("GET", url, nil)
    if not ok then
        return false, data
    end
    if type(data.articles) ~= "table" then
        return false, "Missing articles list in response"
    end
    return true, data.articles
end

function HttpClient.downloadArticle(base_url, rel_path, dest_path)
    local url = normalizeBaseUrl(base_url) .. "/api/articles/" .. HttpClient.encodePathSegments(rel_path)
    return downloadToFile(url, dest_path, READ_TIMEOUT)
end

function HttpClient.listBooks(base_url)
    local url = normalizeBaseUrl(base_url) .. "/api/books"
    local ok, data = requestJson("GET", url, nil)
    if not ok then
        return false, data
    end
    if type(data.books) ~= "table" then
        return false, "Missing books list in response"
    end
    return true, data.books
end

function HttpClient.downloadBook(base_url, rel_path, dest_path)
    local url = normalizeBaseUrl(base_url) .. "/api/books/" .. HttpClient.encodePathSegments(rel_path)
    return downloadToFile(url, dest_path, BOOK_READ_TIMEOUT)
end

function HttpClient.markRead(base_url, rel_path)
    local url = normalizeBaseUrl(base_url) .. "/api/articles/" .. HttpClient.encodePathSegments(rel_path) .. "/mark-read"
    local ok, data = requestJson("POST", url, nil)
    if not ok then
        return false, data
    end
    if data.ok == false then
        return false, tostring(data.error or "mark-read rejected")
    end
    return true, data
end

return HttpClient

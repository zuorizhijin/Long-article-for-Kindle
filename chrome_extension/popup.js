(function () {
  'use strict';

  const DEFAULT_SERVER_URL = 'http://127.0.0.1:8765';
  const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp']);
  const SKIP_DIRS = new Set(['.obsidian', '.git', '.trash', 'node_modules', '.DS_Store']);

  const state = {
    serverUrl: DEFAULT_SERVER_URL,
    serverOnline: false,
    selectedFolder: '',
    folders: [],
    webExtracted: null,
    vaultHandle: null,
    mdFiles: [],     // [{ name, relPath, dirParts, handle }]
    imageFiles: [],  // [{ nameLower, relPathLower, handle }]
    selectedMd: null,
    checkedMdPaths: new Set(),
    syncedMdPaths: new Set(),
    failedMdErrors: new Map(),
    batchRunning: false,
  };

  // DOM helpers
  const $ = (id) => document.getElementById(id);

  function setStatus(msg, type) {
    const bar = $('statusBar');
    if (!msg) {
      bar.classList.add('hidden');
      bar.textContent = '';
      return;
    }
    bar.textContent = msg;
    bar.className = 'status-bar' + (type ? ' ' + type : '');
    bar.classList.remove('hidden');
  }

  function normalizeServerUrl(url) {
    return String(url || DEFAULT_SERVER_URL).trim().replace(/\/+$/, '');
  }

  function normalizeFolder(val) {
    return String(val || '')
      .trim()
      .replace(/\\/g, '/')
      .replace(/^\/+|\/+$/g, '');
  }

  function syncFolderSelect(selectEl, clean) {
    if (!selectEl) return;
    if (clean && !Array.from(selectEl.options).some((opt) => opt.value === clean)) {
      const extra = document.createElement('option');
      extra.value = clean;
      extra.textContent = clean;
      selectEl.appendChild(extra);
    }
    selectEl.value = clean;
  }

  function setFolderValue(val, persist) {
    const clean = normalizeFolder(val);
    state.selectedFolder = clean;
    const label = `保存位置：文章 / ${clean || '根目录'}`;
    if ($('webFolderText')) $('webFolderText').textContent = label;
    if ($('mdFolderText')) $('mdFolderText').textContent = label;
    syncFolderSelect($('webFolderSelect'), clean);
    syncFolderSelect($('mdFolderSelect'), clean);
    if (persist) {
      chrome.storage.local.set({ lastFolder: clean });
    }
  }

  function renderFolderOptions() {
    const selects = [$('webFolderSelect'), $('mdFolderSelect')];
    for (const sel of selects) {
      if (!sel) continue;
      sel.innerHTML = '';
      const rootOpt = document.createElement('option');
      rootOpt.value = '';
      rootOpt.textContent = '根目录';
      sel.appendChild(rootOpt);
      for (const folder of state.folders) {
        const opt = document.createElement('option');
        opt.value = folder;
        opt.textContent = folder;
        sel.appendChild(opt);
      }
      syncFolderSelect(sel, state.selectedFolder);
    }
  }

  async function handlePickSyncFolder() {
    const webBtn = $('webPickFolderBtn');
    const mdBtn = $('mdPickFolderBtn');
    if (webBtn) webBtn.disabled = true;
    if (mdBtn) mdBtn.disabled = true;
    setStatus('请在电脑弹窗中选择或新建分类文件夹...');

    try {
      const res = await chrome.runtime.sendMessage({
        type: 'PICK_SYNC_FOLDER',
        serverUrl: state.serverUrl,
      });
      if (!res || !res.ok) {
        setStatus((res && res.error) || '选择分类文件夹失败', 'err');
        return;
      }
      if (res.cancelled) {
        setStatus('已取消选择分类文件夹');
        return;
      }
      if (Array.isArray(res.folders)) {
        state.folders = res.folders;
        renderFolderOptions();
      } else {
        fetchFolders();
      }
      setFolderValue(res.folder || '', false);
      const destLabel = res.folder ? `文章 / ${res.folder}` : '文章 / 根目录';
      setStatus(`已选择保存位置：${destLabel}`, 'ok');
    } catch (err) {
      setStatus('选择分类文件夹失败：' + (err && err.message ? err.message : err), 'err');
    } finally {
      if (webBtn) webBtn.disabled = false;
      if (mdBtn) mdBtn.disabled = false;
    }
  }

  async function fetchFolders() {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3000);
      const resp = await fetch(`${state.serverUrl}/api/folders`, {
        method: 'GET',
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (!resp.ok) return;
      const data = await resp.json();
      if (data && Array.isArray(data.folders)) {
        state.folders = data.folders;
        renderFolderOptions();
      }
    } catch (_err) {
      // Ignore folder fetch errors when offline
    }
  }

  async function loadSettings() {
    return new Promise((resolve) => {
      chrome.storage.local.get(
        { serverUrl: DEFAULT_SERVER_URL, lastFolder: '' },
        (items) => {
          state.serverUrl = normalizeServerUrl(items.serverUrl || DEFAULT_SERVER_URL);
          $('serverUrlInput').value = state.serverUrl;
          setFolderValue(items.lastFolder || '', false);
          resolve();
        }
      );
    });
  }

  async function saveServerUrl(url) {
    const clean = normalizeServerUrl(url);
    state.serverUrl = clean;
    $('serverUrlInput').value = clean;
    return new Promise((resolve) => {
      chrome.storage.local.set({ serverUrl: clean }, resolve);
    });
  }

  async function checkServerHealth() {
    const badge = $('healthBadge');
    const text = $('healthText');
    badge.className = 'health-badge checking';
    text.textContent = '检测中...';

    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3000);
      const resp = await fetch(`${state.serverUrl}/health`, {
        method: 'GET',
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      if (data && data.ok) {
        state.serverOnline = true;
        badge.className = 'health-badge online';
        const count = typeof data.unread_count === 'number' ? data.unread_count : 0;
        text.textContent = `在线 · 未读 ${count}`;
        updateFallbackHighlights();
        fetchFolders();
        return true;
      }
      throw new Error('Invalid health payload');
    } catch (_err) {
      state.serverOnline = false;
      badge.className = 'health-badge offline';
      text.textContent = '离线（可下载）';
      updateFallbackHighlights();
      return false;
    }
  }

  function updateFallbackHighlights() {
    $('webDownloadBtn').classList.toggle('highlight', !state.serverOnline);
    $('mdDownloadBtn').classList.toggle('highlight', !state.serverOnline);
  }

  // Tab switching
  function switchTab(tabName) {
    const isWeb = tabName === 'web';
    $('tabBtnWeb').classList.toggle('active', isWeb);
    $('tabBtnObsidian').classList.toggle('active', !isWeb);
    $('panelWeb').classList.toggle('hidden', !isWeb);
    $('panelObsidian').classList.toggle('hidden', isWeb);
    setStatus('');
  }

  // Fetch remote image with timeout
  async function fetchImageBytes(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const resp = await fetch(url, { signal: controller.signal });
      clearTimeout(timer);
      if (!resp.ok) return null;
      const mimeType = resp.headers.get('content-type') || '';
      const buf = await resp.arrayBuffer();
      if (!buf || buf.byteLength === 0) return null;
      return {
        data: new Uint8Array(buf),
        mimeType,
      };
    } catch (_err) {
      clearTimeout(timer);
      return null;
    }
  }

  async function fetchImagesWithConcurrency(refs, onProgress) {
    const results = new Array(refs.length).fill(null);
    let nextIdx = 0;
    const workers = Array.from({ length: Math.min(4, refs.length) }, async () => {
      while (nextIdx < refs.length) {
        const i = nextIdx++;
        const ref = refs[i];
        results[i] = await fetchImageBytes(ref.url);
        if (onProgress) onProgress(i + 1, refs.length);
      }
    });
    await Promise.all(workers);
    return results;
  }

  function triggerBlobDownload(epubBytes, filename) {
    const blob = new Blob([epubBytes], { type: 'application/epub+zip' });
    const url = URL.createObjectURL(blob);
    if (chrome.downloads && chrome.downloads.download) {
      chrome.downloads.download(
        {
          url,
          filename,
          saveAs: false,
        },
        () => {
          setTimeout(() => URL.revokeObjectURL(url), 10000);
        }
      );
    } else {
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    }
  }

  async function uploadEpubToServer(epubBytes, filename, title, sourceUrl, folder) {
    const epubBase64 = EpubBuilder.uint8ArrayToBase64(epubBytes);
    const cleanFolder = normalizeFolder(folder);
    const payload = {
      filename,
      epub_base64: epubBase64,
      title,
      source_url: sourceUrl || '',
    };
    if (cleanFolder) {
      payload.folder = cleanFolder;
    }
    const resp = await fetch(`${state.serverUrl}/api/upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`上传失败 (${resp.status}): ${text}`);
    }
    const data = await resp.json();
    if (!data || !data.ok) {
      throw new Error((data && data.error) || '服务端返回失败');
    }
    return data;
  }

  // ========================
  // Mode 1 & 2: Web / X Articles
  // ========================
  async function extractActiveTab() {
    state.webExtracted = null;
    $('webErrorBox').classList.add('hidden');
    $('webPreviewBox').classList.add('hidden');
    $('webSendBtn').disabled = true;
    $('webDownloadBtn').disabled = true;
    $('pageModeBadge').className = 'mode-badge';
    $('pageModeBadge').textContent = '正在分析页面...';

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab || !tab.id || !tab.url || !/^https?:/i.test(tab.url)) {
        $('pageModeBadge').textContent = '非网页标签页';
        $('webErrorBox').textContent = '当前标签页不是 http/https 网页，请切换到文章页面或使用「本地 Obsidian MD」标签页。';
        $('webErrorBox').classList.remove('hidden');
        return;
      }

      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ['content/extractor.js'],
      });

      const [{ result }] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => window.__kindleArticleExtractor && window.__kindleArticleExtractor(),
      });

      if (!result) {
        throw new Error('未能从页面提取内容');
      }

      if (result.mode === 'x') {
        $('pageModeBadge').className = 'mode-badge x-mode';
        $('pageModeBadge').textContent = 'X (Twitter)';
      } else {
        $('pageModeBadge').className = 'mode-badge web-mode';
        $('pageModeBadge').textContent = '网页文章';
      }

      if (!result.ok) {
        $('webErrorBox').textContent = result.message || '当前页面不支持抓取';
        $('webErrorBox').classList.remove('hidden');
        return;
      }

      state.webExtracted = result;
      $('webTitleInput').value = result.title || '';
      $('webAuthorText').textContent = result.author || '-';
      $('webImgCount').textContent = String((result.imageRefs || []).length);

      const tmpDiv = document.createElement('div');
      tmpDiv.innerHTML = result.bodyHtml || '';
      const plain = (tmpDiv.textContent || '').replace(/\s+/g, ' ').trim();
      $('webSnippet').textContent = plain.slice(0, 220) + (plain.length > 220 ? '...' : '');

      $('webPreviewBox').classList.remove('hidden');
      $('webSendBtn').disabled = false;
      $('webDownloadBtn').disabled = false;
    } catch (err) {
      $('pageModeBadge').textContent = '抓取失败';
      $('webErrorBox').textContent = '页面抓取失败：' + (err.message || err);
      $('webErrorBox').classList.remove('hidden');
    }
  }

  async function buildWebEpub() {
    const ext = state.webExtracted;
    if (!ext || !ext.ok) throw new Error('没有可导出的文章内容');

    const title = $('webTitleInput').value.trim() || ext.title || 'Article';
    const refs = ext.imageRefs || [];
    let bodyHtml = ext.bodyHtml || '';
    const images = [];
    let coverId = '';

    if (refs.length > 0) {
      setStatus(`正在下载图片 (0/${refs.length})...`);
      const fetched = await fetchImagesWithConcurrency(refs, (done, total) => {
        setStatus(`正在下载图片 (${done}/${total})...`);
      });

      for (let i = 0; i < refs.length; i++) {
        const ref = refs[i];
        const item = fetched[i];
        if (item && item.data) {
          const { ext: fileExt, mime } = EpubBuilder.guessImageExtension(item.mimeType, ref.url);
          const id = `img_${i}`;
          const filename = `${id}.${fileExt}`;
          images.push({ id, filename, mimeType: mime, data: item.data });
          if (ref.isCover && !coverId) coverId = id;
          bodyHtml = bodyHtml.split(ref.token).join(`images/${filename}`);
        } else {
          bodyHtml = bodyHtml.split(ref.token).join('');
        }
      }
    }

    setStatus('正在打包 EPUB...');
    const epubBytes = await EpubBuilder.buildEpub({
      title,
      author: ext.author,
      sourceUrl: ext.sourceUrl,
      coverId,
      bodyHtml,
      images,
    });
    const filename = EpubBuilder.sanitizeFilename(title, 'article');
    return { epubBytes, filename, title, sourceUrl: ext.sourceUrl };
  }

  async function handleWebAction(sendToServer) {
    $('webSendBtn').disabled = true;
    $('webDownloadBtn').disabled = true;
    try {
      const { epubBytes, filename, title, sourceUrl } = await buildWebEpub();
      if (sendToServer) {
        const folder = state.selectedFolder;
        setStatus('正在发送到电脑同步服务...');
        const res = await uploadEpubToServer(epubBytes, filename, title, sourceUrl, folder);
        const destLabel = folder ? `到「${folder}」分类` : '到根目录';
        setStatus(`已成功发送${destLabel}：${res.filename || filename}`, 'ok');
        checkServerHealth();
      } else {
        triggerBlobDownload(epubBytes, filename);
        setStatus(`已下载 EPUB：${filename}`, 'ok');
      }
    } catch (err) {
      setStatus((err.message || String(err)) + '（可点击「下载 EPUB」保存到本地）', 'err');
      updateFallbackHighlights();
    } finally {
      if (state.webExtracted && state.webExtracted.ok) {
        $('webSendBtn').disabled = false;
        $('webDownloadBtn').disabled = false;
      }
    }
  }

  // ========================
  // Mode 3: Local Obsidian Markdown
  // ========================
  async function scanVaultDirectory(dirHandle) {
    const mdFiles = [];
    const imageFiles = [];

    async function walk(handle, parts) {
      for await (const entry of handle.values()) {
        if (entry.kind === 'directory') {
          if (SKIP_DIRS.has(entry.name) || entry.name.startsWith('.')) continue;
          await walk(entry, parts.concat(entry.name));
        } else if (entry.kind === 'file') {
          const lower = entry.name.toLowerCase();
          const relPath = parts.concat(entry.name).join('/');
          if (lower.endsWith('.md')) {
            mdFiles.push({
              name: entry.name,
              relPath,
              dirParts: parts,
              handle: entry,
            });
          } else {
            const dot = lower.lastIndexOf('.');
            if (dot !== -1 && IMAGE_EXTS.has(lower.slice(dot))) {
              imageFiles.push({
                nameLower: lower,
                relPathLower: relPath.toLowerCase(),
                handle: entry,
              });
            }
          }
        }
      }
    }

    await walk(dirHandle, []);
    mdFiles.sort((a, b) => a.relPath.localeCompare(b.relPath, 'zh-CN'));
    state.mdFiles = mdFiles;
    state.imageFiles = imageFiles;
    state.selectedMd = null;
    state.checkedMdPaths.clear();
    state.syncedMdPaths.clear();
    state.failedMdErrors.clear();
  }

  function getFilteredMdFiles(filterText) {
    const q = String(filterText !== undefined ? filterText : $('mdSearchInput').value || '')
      .trim()
      .toLowerCase();
    return q
      ? state.mdFiles.filter((f) => f.relPath.toLowerCase().includes(q))
      : state.mdFiles;
  }

  function updateBatchBar(matchedFiles) {
    const matched = matchedFiles || getFilteredMdFiles();
    const totalChecked = state.checkedMdPaths.size;
    $('mdCheckedCount').textContent = `已选 ${totalChecked} 篇`;
    const batchBtn = $('mdBatchSendBtn');
    batchBtn.textContent = `批量同步 (${totalChecked})`;
    batchBtn.disabled = state.batchRunning || totalChecked === 0;

    const toggleBtn = $('mdToggleAllBtn');
    const allVisibleChecked =
      matched.length > 0 && matched.every((f) => state.checkedMdPaths.has(f.relPath));
    toggleBtn.textContent = allVisibleChecked ? '取消全选' : '全选';
    toggleBtn.disabled = state.batchRunning || matched.length === 0;
  }

  function toggleMdChecked(relPath) {
    if (state.batchRunning) return;
    if (state.checkedMdPaths.has(relPath)) {
      state.checkedMdPaths.delete(relPath);
    } else {
      state.checkedMdPaths.add(relPath);
    }
  }

  function renderMdFileList(filterText) {
    const listEl = $('mdFileList');
    const matched = getFilteredMdFiles(filterText);

    $('mdFileCount').textContent = `共 ${state.mdFiles.length} 篇笔记，匹配 ${matched.length} 篇（显示前 100 篇）`;
    listEl.innerHTML = '';

    const slice = matched.slice(0, 100);
    for (const item of slice) {
      const li = document.createElement('li');
      if (state.selectedMd && state.selectedMd.relPath === item.relPath) {
        li.className = 'selected';
      }

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = state.checkedMdPaths.has(item.relPath);
      checkbox.disabled = state.batchRunning;
      checkbox.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleMdChecked(item.relPath);
        updateBatchBar(matched);
      });

      const mainDiv = document.createElement('div');
      mainDiv.className = 'file-item-main';

      const titleRow = document.createElement('div');
      titleRow.className = 'file-title-row';

      const titleSpan = document.createElement('span');
      titleSpan.textContent = item.name.replace(/\.md$/i, '');
      titleRow.appendChild(titleSpan);

      if (state.syncedMdPaths.has(item.relPath)) {
        const badge = document.createElement('span');
        badge.className = 'badge-synced';
        badge.textContent = '已同步';
        titleRow.appendChild(badge);
      } else if (state.failedMdErrors.has(item.relPath)) {
        const badge = document.createElement('span');
        badge.className = 'badge-failed';
        badge.textContent = '失败';
        badge.title = state.failedMdErrors.get(item.relPath);
        titleRow.appendChild(badge);
      }

      const pathSpan = document.createElement('span');
      pathSpan.className = 'file-path';
      const errMsg = state.failedMdErrors.get(item.relPath);
      pathSpan.textContent = errMsg ? `${item.relPath} · ${errMsg}` : item.relPath;

      mainDiv.appendChild(titleRow);
      mainDiv.appendChild(pathSpan);

      li.appendChild(checkbox);
      li.appendChild(mainDiv);

      li.addEventListener('click', () => {
        if (state.batchRunning) return;
        toggleMdChecked(item.relPath);
        selectMdFile(item);
      });
      listEl.appendChild(li);
    }

    updateBatchBar(matched);
  }

  async function selectMdFile(item) {
    state.selectedMd = item;
    renderMdFileList($('mdSearchInput').value);
    try {
      const file = await item.handle.getFile();
      const text = await file.text();
      const parsed = MarkdownConverter.convertMarkdown(text, item.name.replace(/\.md$/i, ''));
      $('mdTitleInput').value = parsed.title;
      $('mdSelectedPath').textContent = item.relPath;
      $('mdSelectedBox').classList.remove('hidden');
      $('mdSendBtn').disabled = false;
      $('mdDownloadBtn').disabled = false;
      setStatus('');
    } catch (err) {
      setStatus('读取文件失败：' + (err.message || err), 'err');
    }
  }

  async function activateVaultHandle(handle) {
    state.vaultHandle = handle;
    $('vaultNameText').textContent = `已授权文件夹：${handle.name}`;
    $('grantVaultBtn').classList.add('hidden');
    setStatus('正在扫描 Obsidian 笔记与图片...');
    await scanVaultDirectory(handle);
    $('obsidianSearchWrap').classList.remove('hidden');
    renderMdFileList($('mdSearchInput').value);
    setStatus(`扫描完成：${state.mdFiles.length} 篇 .md，${state.imageFiles.length} 张图片`, 'ok');
  }

  async function initObsidianTab() {
    try {
      const saved = await IdbStore.loadDirectoryHandle();
      if (!saved) return;
      state.vaultHandle = saved;
      const perm = await saved.queryPermission({ mode: 'read' });
      if (perm === 'granted') {
        await activateVaultHandle(saved);
      } else {
        $('vaultNameText').textContent = `已记住文件夹「${saved.name}」（需点击恢复读取权限）`;
        $('grantVaultBtn').classList.remove('hidden');
      }
    } catch (_err) {
      // Ignore IndexedDB errors
    }
  }

  async function resolveLocalObsidianImage(rawPath, mdDirParts) {
    const cleaned = decodeURIComponent(String(rawPath || '').split('#')[0].trim())
      .replace(/\\/g, '/')
      .replace(/^\.\//, '');
    if (!cleaned) return null;

    if (/^https?:\/\//i.test(cleaned)) {
      return fetchImageBytes(cleaned);
    }

    // 1. Try relative to the .md file's directory
    const relFromMd = mdDirParts.concat(cleaned.split('/')).filter(Boolean);
    const normalizedParts = [];
    for (const part of relFromMd) {
      if (part === '..') normalizedParts.pop();
      else if (part !== '.') normalizedParts.push(part);
    }
    const candidate1 = normalizedParts.join('/').toLowerCase();
    const candidate2 = cleaned.replace(/^\/+/, '').toLowerCase();
    const baseName = cleaned.split('/').pop().toLowerCase();

    const found =
      state.imageFiles.find((img) => img.relPathLower === candidate1) ||
      state.imageFiles.find((img) => img.relPathLower === candidate2) ||
      state.imageFiles.find((img) => img.nameLower === baseName);

    if (!found) return null;
    try {
      const file = await found.handle.getFile();
      const buf = await file.arrayBuffer();
      return {
        data: new Uint8Array(buf),
        mimeType: file.type || '',
        filenameHint: file.name,
      };
    } catch (_err) {
      return null;
    }
  }

  async function buildObsidianEpubForItem(item, overrideTitle, onProgress) {
    if (!item) throw new Error('请先选择一篇 .md 笔记');
    const file = await item.handle.getFile();
    const mdText = await file.text();
    const fallbackTitle = item.name.replace(/\.md$/i, '');
    const parsed = MarkdownConverter.convertMarkdown(mdText, fallbackTitle);

    const title = (overrideTitle && overrideTitle.trim()) || parsed.title;
    let bodyHtml = parsed.html;
    const images = [];

    if (parsed.imageRefs.length > 0) {
      if (onProgress) onProgress(`正在解析并嵌入图片 (0/${parsed.imageRefs.length})...`);
      for (let i = 0; i < parsed.imageRefs.length; i++) {
        const ref = parsed.imageRefs[i];
        const imgObj = await resolveLocalObsidianImage(ref.rawPath, item.dirParts);
        if (imgObj && imgObj.data) {
          const { ext, mime } = EpubBuilder.guessImageExtension(
            imgObj.mimeType,
            imgObj.filenameHint || ref.rawPath
          );
          const id = `img_${i}`;
          const filename = `${id}.${ext}`;
          images.push({ id, filename, mimeType: mime, data: imgObj.data });
          const altEsc = EpubBuilder.escapeXml(ref.alt || ref.rawPath);
          bodyHtml = bodyHtml
            .split(ref.token)
            .join(`<img src="images/${filename}" alt="${altEsc}"/>`);
        } else {
          bodyHtml = bodyHtml
            .split(ref.token)
            .join(`<span class="muted-small">[图片未找到: ${EpubBuilder.escapeXml(ref.rawPath)}]</span>`);
        }
        if (onProgress) onProgress(`正在解析并嵌入图片 (${i + 1}/${parsed.imageRefs.length})...`);
      }
    }

    if (onProgress) onProgress('正在打包 EPUB...');
    const sourceUrl = `obsidian://${item.relPath}`;
    const epubBytes = await EpubBuilder.buildEpub({
      title,
      author: parsed.author,
      sourceUrl,
      bodyHtml,
      images,
    });
    const filename = EpubBuilder.sanitizeFilename(title, fallbackTitle);
    return { epubBytes, filename, title, sourceUrl };
  }

  async function buildObsidianEpub() {
    const titleOverride = $('mdTitleInput').value.trim();
    return buildObsidianEpubForItem(state.selectedMd, titleOverride, (msg) => setStatus(msg));
  }

  async function handleObsidianAction(sendToServer) {
    $('mdSendBtn').disabled = true;
    $('mdDownloadBtn').disabled = true;
    try {
      const { epubBytes, filename, title, sourceUrl } = await buildObsidianEpub();
      if (sendToServer) {
        const folder = state.selectedFolder;
        setStatus('正在发送到电脑同步服务...');
        const res = await uploadEpubToServer(epubBytes, filename, title, sourceUrl, folder);
        if (state.selectedMd) {
          state.syncedMdPaths.add(state.selectedMd.relPath);
          state.failedMdErrors.delete(state.selectedMd.relPath);
          state.checkedMdPaths.delete(state.selectedMd.relPath);
          renderMdFileList();
        }
        const destLabel = folder ? `到「${folder}」分类` : '到根目录';
        setStatus(`已成功发送${destLabel}：${res.filename || filename}`, 'ok');
        checkServerHealth();
      } else {
        triggerBlobDownload(epubBytes, filename);
        setStatus(`已下载 EPUB：${filename}`, 'ok');
      }
    } catch (err) {
      setStatus((err.message || String(err)) + '（可点击「下载 EPUB」保存到本地）', 'err');
      updateFallbackHighlights();
    } finally {
      if (state.selectedMd && !state.batchRunning) {
        $('mdSendBtn').disabled = false;
        $('mdDownloadBtn').disabled = false;
      }
    }
  }

  async function handleObsidianBatchSync() {
    if (state.batchRunning) return;
    const targets = state.mdFiles.filter((f) => state.checkedMdPaths.has(f.relPath));
    if (targets.length === 0) return;

    const folder = state.selectedFolder;
    state.batchRunning = true;
    $('mdSendBtn').disabled = true;
    $('mdDownloadBtn').disabled = true;
    renderMdFileList();

    let okCount = 0;
    let failCount = 0;
    const failDetails = [];

    try {
      for (let i = 0; i < targets.length; i++) {
        const item = targets[i];
        const shortName = item.name.replace(/\.md$/i, '');
        setStatus(`正在同步 ${i + 1}/${targets.length}：${shortName}...`);
        try {
          const { epubBytes, filename, title, sourceUrl } = await buildObsidianEpubForItem(item, '');
          await uploadEpubToServer(epubBytes, filename, title, sourceUrl, folder);
          okCount++;
          state.checkedMdPaths.delete(item.relPath);
          state.syncedMdPaths.add(item.relPath);
          state.failedMdErrors.delete(item.relPath);
        } catch (err) {
          failCount++;
          const reason = err && err.message ? err.message : String(err);
          state.failedMdErrors.set(item.relPath, reason);
          failDetails.push(`${shortName}: ${reason}`);
        }
      }
    } finally {
      state.batchRunning = false;
      if (state.selectedMd) {
        $('mdSendBtn').disabled = false;
        $('mdDownloadBtn').disabled = false;
      }
      renderMdFileList();
      checkServerHealth();
    }

    const destLabel = folder ? `到「${folder}」分类` : '到根目录';
    if (failCount === 0) {
      setStatus(`批量同步完成（${destLabel}）：成功 ${okCount} 篇，失败 0 篇`, 'ok');
    } else {
      const previewErr = failDetails.slice(0, 2).join('；');
      setStatus(
        `批量同步完成（${destLabel}）：成功 ${okCount} 篇，失败 ${failCount} 篇（失败项已保留勾选，可直接重试：${previewErr}）`,
        'err'
      );
      updateFallbackHighlights();
    }
  }

  // ========================
  // Event Wiring & Init
  // ========================
  function bindEvents() {
    $('healthBadge').addEventListener('click', checkServerHealth);

    $('toggleSettingsBtn').addEventListener('click', () => {
      $('settingsPanel').classList.toggle('hidden');
    });

    $('saveServerBtn').addEventListener('click', async () => {
      await saveServerUrl($('serverUrlInput').value);
      const ok = await checkServerHealth();
      setStatus(ok ? '服务连接正常' : '无法连接到服务，请检查服务是否启动', ok ? 'ok' : 'err');
    });

    $('tabBtnWeb').addEventListener('click', () => switchTab('web'));
    $('tabBtnObsidian').addEventListener('click', () => switchTab('obsidian'));

    $('webPickFolderBtn').addEventListener('click', handlePickSyncFolder);
    $('mdPickFolderBtn').addEventListener('click', handlePickSyncFolder);
    $('webFolderSelect').addEventListener('change', (e) => setFolderValue(e.target.value, true));
    $('mdFolderSelect').addEventListener('change', (e) => setFolderValue(e.target.value, true));
    $('webFolderRootBtn').addEventListener('click', () => setFolderValue('', true));
    $('mdFolderRootBtn').addEventListener('click', () => setFolderValue('', true));

    if (chrome.storage && chrome.storage.onChanged) {
      chrome.storage.onChanged.addListener((changes, areaName) => {
        if (areaName === 'local' && changes.lastFolder) {
          setFolderValue(changes.lastFolder.newValue || '', false);
          fetchFolders();
        }
      });
    }

    $('reExtractBtn').addEventListener('click', extractActiveTab);
    $('webSendBtn').addEventListener('click', () => handleWebAction(true));
    $('webDownloadBtn').addEventListener('click', () => handleWebAction(false));

    $('pickVaultBtn').addEventListener('click', async () => {
      try {
        const handle = await window.showDirectoryPicker({ mode: 'read' });
        await IdbStore.saveDirectoryHandle(handle);
        await activateVaultHandle(handle);
      } catch (err) {
        if (err && err.name !== 'AbortError') {
          setStatus('授权文件夹失败：' + (err.message || err), 'err');
        }
      }
    });

    $('grantVaultBtn').addEventListener('click', async () => {
      if (!state.vaultHandle) return;
      try {
        const perm = await state.vaultHandle.requestPermission({ mode: 'read' });
        if (perm === 'granted') {
          await activateVaultHandle(state.vaultHandle);
        } else {
          setStatus('未获得读取权限，请重新点击「恢复权限」或「选择文件夹」', 'err');
        }
      } catch (err) {
        setStatus('恢复权限失败：' + (err.message || err), 'err');
      }
    });

    $('openTabBtn').addEventListener('click', () => {
      chrome.runtime.sendMessage({ type: 'OPEN_OBSIDIAN_TAB' });
    });

    $('mdSearchInput').addEventListener('input', (e) => {
      renderMdFileList(e.target.value);
    });

    $('mdToggleAllBtn').addEventListener('click', () => {
      if (state.batchRunning) return;
      const matched = getFilteredMdFiles();
      if (matched.length === 0) return;
      const allVisibleChecked = matched.every((f) => state.checkedMdPaths.has(f.relPath));
      if (allVisibleChecked) {
        for (const f of matched) state.checkedMdPaths.delete(f.relPath);
      } else {
        for (const f of matched) state.checkedMdPaths.add(f.relPath);
      }
      renderMdFileList();
    });

    $('mdBatchSendBtn').addEventListener('click', handleObsidianBatchSync);

    $('mdSendBtn').addEventListener('click', () => handleObsidianAction(true));
    $('mdDownloadBtn').addEventListener('click', () => handleObsidianAction(false));
  }

  async function init() {
    bindEvents();
    await loadSettings();
    checkServerHealth();

    const params = new URLSearchParams(location.search);
    if (params.get('tab') === 'obsidian') {
      switchTab('obsidian');
    } else {
      extractActiveTab();
    }
    initObsidianTab();
  }

  document.addEventListener('DOMContentLoaded', init);
})();

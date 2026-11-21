/**
 * Background service worker for Kindle Article Sync.
 */
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get({ serverUrl: 'http://127.0.0.1:8765' }, (items) => {
    if (!items.serverUrl) {
      chrome.storage.local.set({ serverUrl: 'http://127.0.0.1:8765' });
    }
  });
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || !message.type) {
    return false;
  }

  if (message.type === 'OPEN_OBSIDIAN_TAB') {
    const url = chrome.runtime.getURL('popup.html?tab=obsidian');
    chrome.tabs.create({ url }, () => {
      sendResponse({ ok: true });
    });
    return true;
  }

  if (message.type === 'PICK_SYNC_FOLDER') {
    const baseUrl = String(message.serverUrl || 'http://127.0.0.1:8765')
      .trim()
      .replace(/\/+$/, '');
    (async () => {
      try {
        const resp = await fetch(`${baseUrl}/api/folders/pick`, { method: 'POST' });
        const data = await resp.json().catch(() => null);
        if (!resp.ok || !data || !data.ok) {
          const errMsg = (data && data.error) || `请求失败 (${resp.status})`;
          try {
            sendResponse({ ok: false, error: errMsg });
          } catch (_e) {}
          return;
        }
        if (data.cancelled) {
          try {
            sendResponse({ ok: true, cancelled: true });
          } catch (_e) {}
          return;
        }
        const cleanFolder = String(data.folder || '')
          .trim()
          .replace(/\\/g, '/')
          .replace(/^\/+|\/+$/g, '');
        await chrome.storage.local.set({ lastFolder: cleanFolder });
        try {
          sendResponse({
            ok: true,
            cancelled: false,
            folder: cleanFolder,
            folders: Array.isArray(data.folders) ? data.folders : null,
          });
        } catch (_e) {}
      } catch (err) {
        try {
          sendResponse({ ok: false, error: (err && err.message) || '无法连接到电脑服务' });
        } catch (_e) {}
      }
    })();
    return true;
  }

  return false;
});

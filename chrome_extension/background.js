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

  return false;
});

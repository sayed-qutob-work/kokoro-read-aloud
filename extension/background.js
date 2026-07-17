// Relay for the content script: MV3 content scripts are bound by page CORS,
// but the extension context may fetch http://127.0.0.1:5111 (host_permissions).
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg === "now") {
    fetch("http://127.0.0.1:5111/now")
      .then((r) => r.json())
      .then(sendResponse)
      .catch(() => sendResponse(null));
    return true; // keep sendResponse alive for the async fetch
  }
});

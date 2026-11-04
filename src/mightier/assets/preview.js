(() => {
  "use strict";
  const panel = document.getElementById("mightier-live");
  if (!panel) return;
  const status = document.getElementById("mightier-status");
  const error = document.getElementById("mightier-error");
  const initialVersion = panel.dataset.version;
  const scrollKey = "mightier:scroll:" + panel.dataset.project;
  let savedScroll = null;
  try {
    savedScroll = sessionStorage.getItem(scrollKey);
    sessionStorage.removeItem(scrollKey);
  } catch (_) {
    // Live reload also works when browser storage is disabled.
  }
  if (savedScroll !== null) {
    requestAnimationFrame(() => requestAnimationFrame(() => window.scrollTo(0, Number(savedScroll))));
  }
  async function poll() {
    try {
      const response = await fetch("/__mightier/status", { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) throw new Error("Preview server unavailable");
      const state = await response.json();
      if (state.ready && state.version !== initialVersion) {
        try { sessionStorage.setItem(scrollKey, String(window.scrollY)); } catch (_) { /* Storage is optional. */ }
        window.location.reload();
        return;
      }
      panel.dataset.state = state.error ? "error" : "ready";
      status.textContent = state.error ? "Build failed" : "Live preview";
      error.hidden = !state.error;
      error.textContent = state.error ? "Showing the last valid version.\n" + state.error : "";
    } catch (_) {
      panel.dataset.state = "offline";
      status.textContent = "Reconnecting";
    }
    window.setTimeout(poll, 500);
  }
  poll();
})();

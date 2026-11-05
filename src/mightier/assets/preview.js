(() => {
  "use strict";
  const panel = document.getElementById("mightier-live");
  if (!panel) return;
  const status = document.getElementById("mightier-status");
  const error = document.getElementById("mightier-error");
  const initialVersion = panel.dataset.version;
  const notesView = panel.dataset.view === "notes";
  const scrollKey = "mightier:scroll:" + panel.dataset.project + ":" + (window.location.pathname || "/");
  for (const element of document.querySelectorAll(".entity-reference[data-entity]")) {
    if (element.closest("a")) continue;
    const [kind, id] = element.dataset.entity.split(":");
    if (!["character", "place"].includes(kind) || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id)) continue;
    const link = document.createElement("a");
    link.href = "/__mightier/notes/" + kind + "/" + encodeURIComponent(id);
    link.className = "mightier-entity-link";
    link.title = "View " + kind + " notes";
    element.replaceWith(link);
    link.append(element);
  }
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
      const version = notesView ? state.notes_version : state.version;
      if ((notesView || state.ready) && version !== initialVersion) {
        try { sessionStorage.setItem(scrollKey, String(window.scrollY)); } catch (_) { /* Storage is optional. */ }
        window.location.reload();
        return;
      }
      const message = notesView ? "" : state.error;
      panel.dataset.state = message ? "error" : "ready";
      status.textContent = message ? "Build failed" : (notesView ? "Live notes" : "Live preview");
      error.hidden = !message;
      error.textContent = message ? "Showing the last valid version.\n" + message : "";
    } catch (_) {
      panel.dataset.state = "offline";
      status.textContent = "Reconnecting";
    }
    window.setTimeout(poll, 500);
  }
  poll();
})();

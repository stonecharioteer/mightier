const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const script = fs.readFileSync(process.argv[2], "utf8");

async function run(state, { offline = false, stored = null } = {}) {
  const panel = { dataset: { version: "original", project: "book" } };
  const status = { textContent: "" };
  const error = { textContent: "", hidden: true };
  const storage = new Map(stored === null ? [] : [["mightier:scroll:book", stored]]);
  const scheduled = [];
  const result = { reloaded: false, scroll: null, panel, status, error, storage, scheduled };
  const context = {
    document: { getElementById: (id) => ({ "mightier-live": panel, "mightier-status": status, "mightier-error": error }[id]) },
    sessionStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    requestAnimationFrame: (callback) => callback(),
    fetch: async () => {
      if (offline) throw new Error("Connection closed");
      return { ok: true, json: async () => state };
    },
    window: {
      scrollY: 320,
      scrollTo: (x, y) => { result.scroll = [x, y]; },
      location: { reload: () => { result.reloaded = true; } },
      setTimeout: (callback, delay) => scheduled.push({ callback, delay }),
    },
  };
  vm.runInNewContext(script, context);
  await new Promise(setImmediate);
  return result;
}

(async () => {
  let result = await run({ version: "updated", ready: true, error: "" });
  assert.equal(result.reloaded, true);
  assert.equal(result.storage.get("mightier:scroll:book"), "320");
  result = await run({ version: "original", ready: true, error: "" }, { stored: "140" });
  assert.deepEqual(result.scroll, [0, 140]);
  assert.equal(result.status.textContent, "Live preview");
  assert.equal(result.scheduled[0].delay, 500);
  result = await run({ version: "original", ready: true, error: "<script>bad input</script>" });
  assert.equal(result.reloaded, false);
  assert.equal(result.error.hidden, false);
  assert.equal(result.panel.dataset.state, "error");
  assert.match(result.error.textContent, /<script>bad input<\/script>/);
  result = await run(null, { offline: true });
  assert.equal(result.status.textContent, "Reconnecting");
  assert.equal(result.scheduled[0].delay, 500);
  console.log("Live reload client checks passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });

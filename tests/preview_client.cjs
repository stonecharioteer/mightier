const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const script = fs.readFileSync(process.argv[2], "utf8");

async function run(state, { offline = false, stored = null, view = "book", references = [] } = {}) {
  const panel = { dataset: { version: "original", project: "book", view } };
  const status = { textContent: "" };
  const error = { textContent: "", hidden: true };
  const storage = new Map(stored === null ? [] : [["mightier:scroll:book:/", stored]]);
  const scheduled = [];
  const result = { reloaded: false, scroll: null, panel, status, error, storage, scheduled };
  const context = {
    document: {
      getElementById: (id) => ({ "mightier-live": panel, "mightier-status": status, "mightier-error": error }[id]),
      querySelectorAll: () => references,
      createElement: (tag) => ({ tag, append(element) { this.child = element; } }),
    },
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
      location: { pathname: "/", reload: () => { result.reloaded = true; } },
      setTimeout: (callback, delay) => scheduled.push({ callback, delay }),
    },
  };
  vm.runInNewContext(script, context);
  await new Promise(setImmediate);
  return result;
}

function reference(entity, insideLink = false) {
  return {
    dataset: { entity },
    closest: () => insideLink ? {} : null,
    replaceWith(link) { this.link = link; },
  };
}

(async () => {
  let result = await run({ version: "updated", ready: true, error: "" });
  assert.equal(result.reloaded, true);
  assert.equal(result.storage.get("mightier:scroll:book:/"), "320");
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

  const person = reference("character:john-doe");
  const place = reference("place:greyhaven");
  const nested = reference("character:john-doe", true);
  const invalid = reference("character:../../private");
  await run({ version: "original", ready: true, error: "" }, { references: [person, place, nested, invalid] });
  assert.equal(person.link.href, "/__mightier/notes/character/john-doe");
  assert.equal(person.link.child, person);
  assert.equal(place.link.href, "/__mightier/notes/place/greyhaven");
  assert.equal(nested.link, undefined);
  assert.equal(invalid.link, undefined);
  result = await run({ version: "original", notes_version: "updated", ready: false, error: "Book is invalid" }, { view: "notes" });
  assert.equal(result.reloaded, true);
  result = await run({ version: "updated", notes_version: "original", ready: false, error: "Book is invalid" }, { view: "notes" });
  assert.equal(result.reloaded, false);
  assert.equal(result.status.textContent, "Live notes");
  assert.equal(result.error.hidden, true);
  console.log("Live reload client checks passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });

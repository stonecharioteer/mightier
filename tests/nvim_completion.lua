local root = assert(vim.env.MIGHTIER_TEST_ROOT)
local source_root = assert(vim.env.MIGHTIER_SOURCE_ROOT)
vim.opt.runtimepath:append(source_root)
vim.g.maplocalleader = ","

local plugin = require "mightier"
plugin.setup { command = { assert(vim.env.MIGHTIER_TEST_PYTHON), "-m", "mightier" } }
local Source = require "mightier.source"
local source = Source.new {}
local warnings = {}
vim.notify = function(message) warnings[#warnings + 1] = message end
local assertions = 0
local function equal(expected, actual)
  assertions = assertions + 1
  assert(vim.deep_equal(expected, actual), "Expected " .. vim.inspect(expected) .. ", got " .. vim.inspect(actual))
end

local buffer = vim.api.nvim_create_buf(true, false)
vim.api.nvim_set_current_buf(buffer)
vim.api.nvim_buf_set_name(buffer, root .. "/chapters/01-arrival.md")
vim.bo[buffer].filetype = "markdown"
equal(true, plugin.contains(buffer))
equal(true, source:enabled())

local function get(line, col)
  local response
  source:get_completions(
    { bufnr = buffer, cursor = { 1, col or #line }, line = line },
    function(value) response = value end
  )
  assert(vim.wait(20000, function() return response ~= nil end, 10), "Completion timed out")
  return response.items
end

local function labels(items)
  local result = {}
  for _, value in ipairs(items) do
    result[#result + 1] = value.label
  end
  table.sort(result)
  return result
end

local function find(items, label)
  for _, value in ipairs(items) do
    if value.label == label then return value end
  end
  error("Missing completion: " .. label)
end

local function apply(line, selected)
  vim.api.nvim_buf_set_lines(buffer, 0, -1, false, { line })
  vim.lsp.util.apply_text_edits({ selected.textEdit }, buffer, "utf-8")
  return vim.api.nvim_buf_get_lines(buffer, 0, 1, false)[1]
end

equal({ "character:", "place:" }, labels(get "{{"))
equal("{{character:", find(get "{{c", "character:").textEdit.newText)
equal({}, get "Ordinary prose")
equal({}, get "{{{c")
equal({}, get "{{animal:")
equal({}, get "{{character:john-doe.short}}")

local character = find(get "{{character:jo", "john-doe")
equal("{{character:john-doe.", character.textEdit.newText)
equal("Jöhn Doe", character.detail)
equal("A physician.", character.documentation.value)
local place = find(get "{{place:", "greyhaven")
equal("{{place:greyhaven.", place.textEdit.newText)
equal({ "full", "nickname", "short", "surname" }, labels(get "{{character:john-doe."))
equal({ "name" }, labels(get "{{place:greyhaven."))
equal({}, get "{{character:unknown.")

local line = "Éva met {{character:john-doe.sh}}."
local col = #"Éva met {{character:john-doe.sh"
local selected = find(get(line, col), "short")
equal("Éva met {{character:john-doe.short}}.", apply(line, selected))
equal("Jöhn", selected.detail)
line = "{{character:jo}} next"
equal("{{character:john-doe. next", apply(line, find(get(line, #"{{character:jo"), "john-doe")))
line = "{{c}}"
equal("{{character:", apply(line, find(get(line, 3), "character:")))
line = "prefix {{character:john-doe.sh next"
equal(
  "prefix {{character:john-doe.short}} next",
  apply(line, find(get(line, #"prefix {{character:john-doe.sh"), "short"))
)

equal(true, vim.b[buffer].mightier_mapped)
local mappings = {}
for _, map in ipairs(vim.api.nvim_buf_get_keymap(buffer, "n")) do
  mappings[map.lhs] = map
end
assert(mappings[",c"] and mappings[",p"], "Local leader mappings are missing")
equal(true, mappings[",c"].callback():find("i{{character:", 1, true) == 1)
equal(true, mappings[",p"].callback():find("i{{place:", 1, true) == 1)

-- Saved changes, new records, and deleted records invalidate the registry cache.
local character_path = assert(vim.env.MIGHTIER_TEST_METADATA) .. "/characters/john-doe.md"
local lines = vim.fn.readfile(character_path)
for index, text in ipairs(lines) do
  if text:match "^  short:" then lines[index] = "  short: Johnny" end
end
vim.fn.writefile(lines, character_path)
equal("Johnny", find(get "{{character:john-doe.", "short").detail)
local new_path = assert(vim.env.MIGHTIER_TEST_METADATA) .. "/places/new-town.md"
vim.fn.writefile({ "---", "id: new-town", "type: place", "names:", "  name: New Town", "---" }, new_path)
equal({ "greyhaven", "new-town" }, labels(get "{{place:"))
vim.uv.fs_unlink(new_path)
equal({ "greyhaven" }, labels(get "{{place:"))

-- One incomplete record must not hide the valid records.
vim.fn.writefile({ "---", "id: broken" }, new_path)
equal({ "greyhaven" }, labels(get "{{place:"))
equal(1, #warnings)
assert(warnings[1]:find("mightier check", 1, true))
vim.uv.fs_unlink(new_path)

-- Cancelled requests do not receive late callbacks. The shared cache still fills.
local called = false
local cancel = source:get_completions(
  { bufnr = buffer, cursor = { 1, 8 }, line = "{{place:" },
  function() called = true end
)
cancel()
assert(vim.wait(20000, function() return not source.registries[plugin.project_root(buffer)].loading end, 10))
equal(false, called)
equal({ "greyhaven" }, labels(get "{{place:"))

-- One completion source supports separate book projects with shared metadata.
vim.api.nvim_buf_set_name(buffer, assert(vim.env.MIGHTIER_OTHER_ROOT) .. "/chapters/01-arrival.md")
equal(true, plugin.contains(buffer))
equal("Johnny", find(get "{{character:john-doe.", "short").detail)
equal(2, vim.tbl_count(source.registries))

-- Scope is a path boundary, not a matching directory-name prefix.
vim.api.nvim_buf_set_name(buffer, root .. "-other/chapter.md")
equal(false, plugin.contains(buffer))
equal(false, source:enabled())
equal({}, get "{{c")
equal(nil, vim.b[buffer].mightier_mapped)
for _, map in ipairs(vim.api.nvim_buf_get_keymap(buffer, "n")) do
  assert(map.lhs ~= ",c" and map.lhs ~= ",p", "Mapping leaked outside the repository")
end
vim.api.nvim_buf_set_name(buffer, root .. "/chapters/01-arrival.md")
vim.bo[buffer].filetype = "lua"
equal(false, plugin.contains(buffer))
equal(nil, vim.b[buffer].mightier_mapped)
vim.bo[buffer].filetype = "markdown"
equal(true, vim.b[buffer].mightier_mapped)
plugin.setup { command = { assert(vim.env.MIGHTIER_TEST_PYTHON), "-m", "mightier" } }
equal(true, vim.b[buffer].mightier_mapped)

print("Neovim completion checks passed: " .. assertions)

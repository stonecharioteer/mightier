-- Run with -c luafile so Neovim can process real insert-mode input events.
local root = assert(vim.env.MIGHTIER_TEST_ROOT)
vim.opt.runtimepath:append(assert(vim.env.MIGHTIER_SOURCE_ROOT))
vim.opt.runtimepath:append(assert(vim.env.MIGHTIER_BLINK))
vim.g.maplocalleader = ","
vim.opt.swapfile = false
require("mightier").setup { command = { assert(vim.env.MIGHTIER_TEST_PYTHON), "-m", "mightier" } }
local blink = require "blink.cmp"
blink.setup {
  fuzzy = { implementation = "lua" },
  sources = {
    default = { "mightier" },
    providers = {
      mightier = { name = "Mightier", module = "mightier.source", opts = {} },
    },
  },
  completion = {
    list = { selection = { preselect = false, auto_insert = true } },
    documentation = { auto_show = false },
  },
}
vim.cmd.edit(vim.fn.fnameescape(root .. "/chapters/01-arrival.md"))
vim.bo.filetype = "markdown"
vim.api.nvim_buf_set_lines(0, 0, -1, false, { "" })
local steps = {}
local index = 0
local function line() return vim.api.nvim_get_current_line() end
local function find(label)
  for position, value in ipairs(blink.get_items()) do
    if value.source_id == "mightier" and value.label == label then return position end
  end
end
local function accept(label) assert(blink.accept { index = assert(find(label), "Missing completion: " .. label) }) end
local function add(action, ready) steps[#steps + 1] = { action = action, ready = ready } end

add(function() vim.cmd.startinsert() end, function() return vim.api.nvim_get_mode().mode == "i" end)
add(function() vim.api.nvim_input "{{c" end, function() return blink.is_visible() and find "character:" end)
add(function() accept "character:" end, function() return line() == "{{character:" and find "john-doe" end)
add(function() accept "john-doe" end, function() return line() == "{{character:john-doe." and find "short" end)
add(function() accept "short" end, function() return line() == "{{character:john-doe.short}}" end)
add(
  function() vim.api.nvim_input "<Esc>0,p" end,
  function() return vim.api.nvim_get_mode().mode == "i" and line():sub(1, 8) == "{{place:" and find "greyhaven" end
)
add(function() accept "greyhaven" end, function() return line():sub(1, 18) == "{{place:greyhaven." and find "name" end)
add(
  function() accept "name" end,
  function() return line() == "{{place:greyhaven.name}}{{character:john-doe.short}}" end
)
add(
  function() vim.api.nvim_input "<Esc>o{{character:jo" end,
  function() return line() == "{{character:jo" and find "john-doe" end
)
add(function() accept "john-doe" end, function() return line() == "{{character:john-doe." and find "short" end)
add(function() vim.api.nvim_input "sh" end, function() return line() == "{{character:john-doe.sh" and find "short" end)
add(function() accept "short" end, function() return line() == "{{character:john-doe.short}}" end)
add(
  function() vim.api.nvim_input "<Esc>o<Esc>,c" end,
  function() return line() == "{{character:" and vim.api.nvim_get_mode().mode == "i" and find "john-doe" end
)
add(function() accept "john-doe" end, function() return line() == "{{character:john-doe." and find "nickname" end)
add(function() accept "nickname" end, function() return line() == "{{character:john-doe.nickname}}" end)

local started
local function fail(message)
  io.stderr:write("Blink integration step " .. index .. ": " .. tostring(message) .. "\n")
  io.stderr:write("Line: " .. line() .. "\nItems: " .. vim.inspect(blink.get_items()) .. "\n")
  vim.cmd "cquit 1"
end
local poll
local function advance()
  index = index + 1
  if index > #steps then
    print "Blink insert-mode completion checks passed"
    vim.cmd "qa!"
    return
  end
  started = vim.uv.hrtime()
  local ok, error = pcall(steps[index].action)
  if not ok then
    fail(error)
    return
  end
  vim.defer_fn(poll, 20)
end
poll = function()
  local ok, ready = pcall(steps[index].ready)
  if not ok then
    fail(ready)
    return
  end
  if ready then
    advance()
    return
  end
  if (vim.uv.hrtime() - started) / 1e9 > 15 then
    fail "Timed out"
    return
  end
  vim.defer_fn(poll, 20)
end
vim.schedule(advance)

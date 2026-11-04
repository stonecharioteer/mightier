local M = { command = { "mightier" } }

function M.project_root(bufnr)
  bufnr = bufnr == 0 and vim.api.nvim_get_current_buf() or bufnr
  if not vim.api.nvim_buf_is_valid(bufnr) or vim.bo[bufnr].filetype ~= "markdown" then return end
  if vim.bo[bufnr].buftype ~= "" then return end
  local name = vim.api.nvim_buf_get_name(bufnr)
  if name == "" then return end
  local path = vim.fs.normalize(vim.fn.resolve(name))
  local marker = vim.fs.find("mightier.toml", { path = vim.fs.dirname(path), upward = true, type = "file" })[1]
  if marker then return vim.fs.dirname(marker) end
end

function M.contains(bufnr) return M.project_root(bufnr) ~= nil end

function M.setup(opts)
  opts = opts or {}
  M.command = opts.command or { "mightier" }
  local function attach(bufnr)
    if M.contains(bufnr) then
      for key, kind in pairs { c = "character", p = "place" } do
        local tag_kind = kind
        vim.keymap.set(
          "n",
          "<LocalLeader>" .. key,
          function()
            return "i{{" .. tag_kind .. ":<Cmd>lua require('blink.cmp').show({ providers = { 'mightier' } })<CR>"
          end,
          { buffer = bufnr, expr = true, desc = "Insert " .. tag_kind .. " tag" }
        )
      end
      vim.b[bufnr].mightier_mapped = true
    elseif vim.b[bufnr].mightier_mapped then
      for _, key in ipairs { "c", "p" } do
        pcall(vim.keymap.del, "n", "<LocalLeader>" .. key, { buffer = bufnr })
      end
      vim.b[bufnr].mightier_mapped = nil
    end
  end
  local group = vim.api.nvim_create_augroup("MightierCompletion", { clear = true })
  vim.api.nvim_create_autocmd({ "FileType", "BufEnter", "BufFilePost", "BufWritePost" }, {
    group = group,
    callback = function(event) attach(event.buf) end,
  })
  for _, bufnr in ipairs(vim.api.nvim_list_bufs()) do
    attach(bufnr)
  end
end

return M

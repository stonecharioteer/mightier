-- Lazy.nvim plugin specification. Install the Mightier CLI separately.
return {
  "saghen/blink.cmp",
  dependencies = {
    {
      "stonecharioteer/mightier",
      lazy = false,
      main = "mightier",
      opts = { command = { "mightier" } },
    },
  },
  opts = function(_, opts)
    opts.sources = opts.sources or {}
    opts.sources.default = opts.sources.default or { "lsp", "path", "snippets", "buffer" }
    if not vim.tbl_contains(opts.sources.default, "mightier") then table.insert(opts.sources.default, "mightier") end
    opts.sources.providers = opts.sources.providers or {}
    opts.sources.providers.mightier = {
      name = "Mightier",
      module = "mightier.source",
      async = true,
      score_offset = 100,
    }
  end,
}

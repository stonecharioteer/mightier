local Source = {}
Source.__index = Source

function Source.new() return setmetatable({ registries = {} }, Source) end

function Source:enabled() return require("mightier").contains(vim.api.nvim_get_current_buf()) end

function Source:get_trigger_characters() return { "{", ":", ".", "-", "_" } end

local function tag_context(ctx)
  local before = ctx.line:sub(1, ctx.cursor[2])
  local start, body = before:match "(){{([a-z0-9:._%-]*)$"
  if not start or before:sub(start - 1, start - 1) == "{" then return end
  local stop = ctx.cursor[2]
  -- Replace the rest of an existing tag, including auto-paired closing braces.
  -- Without closing braces, preserve text to the right of a newly inserted tag.
  local tail = ctx.line:sub(stop + 1):match "^[a-z0-9:._%-]*}}"
  if tail then stop = stop + #tail end
  return {
    body = body,
    range = {
      start = { line = ctx.cursor[1] - 1, character = start - 1 },
      ["end"] = { line = ctx.cursor[1] - 1, character = stop },
    },
  }
end

local function item(tag, label, text, detail, description, continue)
  if description == "" then description = nil end
  return {
    label = label,
    kind = vim.lsp.protocol.CompletionItemKind.Text,
    filterText = text:sub(3),
    detail = detail,
    documentation = { kind = "plaintext", value = description or detail or text },
    insertTextFormat = vim.lsp.protocol.InsertTextFormat.PlainText,
    textEdit = { newText = text, range = vim.deepcopy(tag.range) },
    data = { mightier_continue = continue or false },
  }
end

function Source:get_completions(ctx, callback)
  local function respond(items) callback { items = items, is_incomplete_forward = true, is_incomplete_backward = true } end
  local plugin = require "mightier"
  local root = plugin.project_root(ctx.bufnr)
  if not root then
    respond {}
    return
  end
  local tag = tag_context(ctx)
  if not tag then
    respond {}
    return
  end

  if tag.body:match "^[a-z]*$" then
    respond {
      item(tag, "character:", "{{character:", "Character names", nil, true),
      item(tag, "place:", "{{place:", "Place names", nil, true),
    }
    return
  end

  local kind, entity_id = tag.body:match "^([a-z]+):([a-z0-9%-]*)$"
  local form
  if not kind then
    kind, entity_id, form = tag.body:match "^([a-z]+):([a-z0-9%-]+)%.([a-z0-9_-]*)$"
  end
  if kind ~= "character" and kind ~= "place" then
    respond {}
    return
  end

  self.registries[root] = self.registries[root] or require("mightier.registry").new(root, plugin.command)
  return self.registries[root]:get(function(records)
    local items = {}
    for _, record in ipairs(records) do
      if record.type == kind then
        if form == nil then
          local name = record.names.full or record.names.name or record.id
          items[#items + 1] =
            item(tag, record.id, "{{" .. kind .. ":" .. record.id .. ".", name, record.description, true)
        elseif record.id == entity_id then
          for key, name in pairs(record.names) do
            items[#items + 1] =
              item(tag, key, "{{" .. kind .. ":" .. entity_id .. "." .. key .. "}}", name, record.description)
          end
        end
      end
    end
    table.sort(items, function(a, b) return a.label < b.label end)
    respond(items)
  end)
end

function Source:execute(ctx, selected, callback, default_implementation)
  default_implementation()
  callback()
  if selected.data and selected.data.mightier_continue then
    vim.schedule(function()
      if self:enabled() and vim.api.nvim_get_mode().mode:sub(1, 1) == "i" then
        require("blink.cmp").show { providers = { "mightier" } }
      end
    end)
  end
end

return Source

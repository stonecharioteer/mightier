local Registry = {}
Registry.__index = Registry

function Registry.new(root, command, metadata_only)
  return setmetatable({
    root = root,
    command = command,
    waiters = {},
    metadata_only = metadata_only,
    metadata_dir = metadata_only and root or nil,
  }, Registry)
end

function Registry:signature()
  local parts = {}
  local function add(path)
    local stat = vim.uv.fs_stat(path)
    if stat then parts[#parts + 1] = table.concat({ path, stat.size, stat.mtime.sec, stat.mtime.nsec }, ":") end
  end
  if not self.metadata_only then add(self.root .. "/mightier.toml") end
  if self.metadata_dir then
    for _, folder in ipairs { "characters", "places" } do
      local directory = self.metadata_dir .. "/" .. folder
      local scan = vim.uv.fs_scandir(directory)
      if scan then
        while true do
          local name = vim.uv.fs_scandir_next(scan)
          if not name then break end
          if name:match "%.md$" then add(directory .. "/" .. name) end
        end
      end
    end
  end
  table.sort(parts)
  return table.concat(parts, "\n")
end

function Registry:get(callback)
  local signature = self:signature()
  local metadata_before = self.metadata_dir
  if self.cached_signature == signature then
    callback(self.records)
    return function() end
  end
  local waiter = { callback = callback, active = true }
  self.waiters[#self.waiters + 1] = waiter
  if not self.loading then
    self.loading = true
    local function finish(records, warning)
      self.loading = false
      -- Do not mark a snapshot taken after the reader ran as already loaded.
      -- Saves during a request must invalidate the next completion request.
      self.cached_signature = metadata_before == self.metadata_dir and signature or nil
      self.records = records
      local waiters = self.waiters
      self.waiters = {}
      if warning then vim.notify(warning, vim.log.levels.WARN, { title = "Mightier completion" }) end
      for _, pending in ipairs(waiters) do
        if pending.active then pending.callback(records) end
      end
    end
    if vim.fn.executable(self.command[1]) ~= 1 then
      finish({}, "The Mightier command is missing. Install it with uv tool install, then restart Neovim.")
    else
      local parameters = self.metadata_only and { "completion-data", "--metadata-dir", self.root }
        or { "--project", self.root, "completion-data" }
      local arguments = vim.list_extend(vim.deepcopy(self.command), parameters)
      vim.system(
        arguments,
        { cwd = self.root, text = true, timeout = 30000 },
        vim.schedule_wrap(function(result)
          if result.code ~= 0 then
            finish({}, "Could not load metadata: " .. vim.trim(result.stderr or "Run mightier check."))
            return
          end
          local ok, data = pcall(vim.json.decode, result.stdout)
          if
            not ok
            or type(data) ~= "table"
            or type(data.records) ~= "table"
            or type(data.metadata_dir) ~= "string"
          then
            finish({}, "The metadata reader returned invalid data. Run mightier check.")
            return
          end
          self.metadata_dir = data.metadata_dir
          local warning
          if data.errors and #data.errors > 0 then
            warning = "Some metadata files are invalid. Run mightier check.\n" .. table.concat(data.errors, "\n")
          end
          finish(data.records, warning)
        end)
      )
    end
  end
  return function() waiter.active = false end
end

return Registry

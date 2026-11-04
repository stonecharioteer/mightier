# Mightier

<img src="src/mightier/assets/mightier.svg" alt="A pixelated sword with a fountain-pen nib" width="128" height="128">

> The Pen is *mightier* than the Sword.

A small book-writing CLI. Write Markdown, share character and place notes, build
EPUBs, and read a live HTML preview while you edit.

## Install

Mightier requires stable Python 3.14 or later. `uv` can provide the interpreter.
Install [uv](https://docs.astral.sh/uv/) first, then:

```sh
uv tool install --python 3.14 git+https://github.com/stonecharioteer/mightier
mightier setup
```

Or run without a permanent installation:

```sh
uvx --python 3.14 --from git+https://github.com/stonecharioteer/mightier mightier --help
```

For a repeatable install, append `@COMMIT` to the Git URL. This project is not
published to PyPI yet. `uvx mightier` alone is not the documented install method.

`mightier setup` uses Homebrew to install missing Pandoc and EPUBCheck commands.
It leaves available tools unchanged. Homebrew also installs EPUBCheck's Java
dependency. Homebrew and uv are initial prerequisites, not installed by the CLI.

## Initialize a book

Each book folder is a separate project:

```sh
mightier init books/book-01 --metadata-dir metadata
mightier init books/book-02 --metadata-dir metadata
```

`--metadata-dir` is relative to the command's working directory. The example
shares one `metadata/` directory across both books. If omitted, each book gets
its own `metadata/` directory.

```text
metadata/
  characters/
  places/
books/
  book-01/
    mightier.toml
    chapters/
      01-arrival.md
  book-02/
    mightier.toml
    chapters/
      01-arrival.md
```

Edit the generated `mightier.toml`:

```toml
schema-version = 1
title = "The long return"
author = "Your name"
lang = "en"
metadata-dir = "../../metadata"
bibliography = "../../metadata/references.bib"
chapters = [
  "chapters/01-arrival.md",
  "chapters/02-departure.md",
]
```

All stored relative paths resolve from the book folder, regardless of where you
run the command. Absolute metadata paths also work. Chapters must stay inside
the book folder. The chapter list sets the publication order.

Initialization preserves existing content. It does not overwrite an existing
configuration or chapter. Replace the sample author, title, and opening
paragraph before publication.

## Write and build

From a book folder:

```sh
mightier new character john-doe
mightier new place greyhaven
mightier new reference doe2024
mightier check
mightier build
mightier build --format html
mightier serve
```

- `build` writes `build/book.epub`. It runs EPUBCheck when available.
- `build --format html` writes a standalone `build/book.html`.
- `serve` prints a loopback URL, normally `http://127.0.0.1:8000/`.
- `clean` removes that book's `build/` only.

Commands find the nearest parent `mightier.toml`. You can also select a book:

```sh
mightier --project books/book-01 build
mightier --project books/book-02 serve --port 8001 --open
```

There is no workspace configuration or multi-book build command. No Python
project, virtual environment, Justfile, or tooling source is needed in a writing
repository.

## Shared names and private notes

Character and place records are Markdown files with YAML front matter. For
example, `metadata/characters/john-doe.md`:

```markdown
---
id: john-doe
type: character
names:
  full: John Doe
  short: John
  surname: Doe
public_description: A physician who lives in Greyhaven.
---

# Background

Born in Greyhaven.

# Relationships

Distrusts the harbour master.

# Story notes

His account of the fire is false.
```

Place records use `type: place` and provide `names.name` by default. Add more name
forms as needed. Use them in chapters:

```markdown
# Arrival

{{character:john-doe.full}} returned to {{place:greyhaven.name}}.

"Wait, {{character:john-doe.short}}," she said.
```

Keep IDs equal to filenames without `.md`. Rename a character by changing its
name fields, not its ID. Names are literal text. The tool does not guess
possessives or pronouns.

Unknown IDs, missing name forms, duplicate YAML keys, and unfinished prose tags
fail the check and build. Tags in code, raw markup, math, and link targets remain
literal. A failed build preserves the previous output.

Chapter prose, resolved names, cited bibliography entries, and the configured
title, author, and language enter published output. Metadata notes and descriptions stay private.
Chapter front matter does not enter output metadata. Notes placed directly in
chapter prose will be published.

References retain their IDs and name forms in the document structure. Glossaries
and appearance indexes can be added later. They are not generated yet.

## Edit individual records

```sh
mightier edit character john-doe
mightier edit place greyhaven
mightier edit reference doe2024
mightier edit book
```

These commands use `$VISUAL`, then `$EDITOR`, then Neovim or vi. Character and
place records each have their own Markdown file. Add or update YAML fields and
write notes below the front matter. The reference command opens the shared
BibTeX file at the selected entry when the editor supports line jumps. The book
command opens `mightier.toml`.

Mightier validates the saved data when the editor exits. Invalid edits are kept
so you can correct them. No second copy or editor-specific data store is used.
Direct edits with your usual editor work too.

## References

`init` creates an empty shared `references.bib` and records its path in
`mightier.toml`. `new reference KEY` appends a placeholder BibTeX entry without
replacing existing entries. Edit the title, author, date, and other details.

Use Pandoc citations in chapter prose:

```markdown
This account follows the earlier study [@doe2024].
```

Pandoc's citeproc resolves citations and adds a reference list. Unknown citation
keys fail validation. Several books can use the same bibliography, or you can
point `bibliography` at a separate BibTeX file. Relative paths resolve from the
book folder.

## Live preview

`mightier serve` rebuilds HTML after saved changes to chapters, local assets,
`mightier.toml`, the configured metadata directory, or the bibliography. The
browser reloads after a successful build and preserves its scroll position.

If a build fails, the last valid page stays visible. The page shows the error
and recovers after you fix and save the source. It also reconnects if the server
restarts. Use `--open` to open your browser, or `--port` to choose a port.

The server binds to `127.0.0.1`. It serves only generated HTML and its preview
assets. It does not expose a directory listing, chapters, configuration, or
private metadata files. Stop it with Ctrl-C.

## Neovim completion

The same repository contains a blink.cmp source under `lua/mightier/`. Configure
it as a local Lazy plugin or use `stonecharioteer/mightier` as the plugin source.
See [the example configuration](examples/neovim.lua).

The default command is the installed `mightier` CLI. You can instead set
`command` to a list such as:

```lua
{ "uvx", "--python", "3.14", "--from", "git+https://github.com/stonecharioteer/mightier", "mightier" }
```

The source finds the current book from its nearest `mightier.toml`. It reads
shared metadata through the CLI, not through code in the writing repository.
Suggestions start at `{{` or `{{c` and continue through type, ID, and name form.
Selecting a form inserts the closing `}}` without duplicating existing braces.

With a comma local leader, `,c` and `,p` start character and place tags in normal
mode and enter insert mode. Mappings apply only to Markdown buffers inside a
book project. Saved metadata changes refresh the suggestions. Invalid records
produce a warning without hiding valid records. Private notes do not enter
completion data.

## Help and logo

Both `mightier --help` and `mightier help` show the logo and quote. Supported
image terminals display the bundled PNG. Other terminals use a coloured block
rendering of the same SVG. Redirected output uses a plain-text fallback.

The SVG has a transparent background and follows a four-pixel grid:
[`mightier.svg`](src/mightier/assets/mightier.svg).

## Logs

Every CLI invocation writes DEBUG-level diagnostics to `mightier.log` in its
current working directory, even when `--project` selects another folder. The log
records commands, paths, build stages, subprocess results, and error tracebacks.
It does not record full manuscript text or private note bodies.

The active file and one backup, `mightier.log.1`, are each limited to 5 MB. The
combined log size stays below 10 MB. Rotation is byte-based and coordinated
between preview and editor processes. A hidden `.mightier-log.lock` file holds
the process lock. Preview ignores all of these files.

Reduce or disable file logging with:

```sh
mightier --log-level info build
mightier --log-level off serve
```

Logs have owner-only permissions. Keep `mightier.log*` and `.mightier-log.lock`
out of version control. Generated book projects include these ignore rules.
Mightier currently targets macOS and Linux.

## Development

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv build
```

Development uses stable Python 3.14. The CLI uses Rich for colour and progress.
Tests use temporary book projects and fake Homebrew commands. They do not install Homebrew packages. Pandoc tests require
Pandoc. Editor tests require Neovim and use the local blink.cmp installation when
available. Preview tests cover file changes, error recovery, shared metadata,
and the HTTP endpoints.

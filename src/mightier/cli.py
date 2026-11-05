"""The Mightier command-line interface."""

# PYTHON_ARGCOMPLETE_OK

import argparse
import json
import sys
from pathlib import Path

import argcomplete
from argcomplete.completers import DirectoriesCompleter, SuppressCompleter

from mightier import __version__
from mightier.books import ENTITY_FOLDERS, BookError, Metadata, Project
from mightier.completion import collect
from mightier.logging_config import LEVELS, configure, logger
from mightier.output import console, failure, warning
from mightier.setup import setup
from mightier.shell_completion import (
    SHELLS,
    chapter_names,
    no_values,
    record_names,
    script,
)


class HelpParser(argparse.ArgumentParser):
    def print_help(self, file=None):
        from mightier.logo import help_header

        if file is None or file is sys.stdout:
            help_header()
            console.print(self.format_help(), markup=False, end="")
        else:
            super().print_help(file)


def resource(commands, name, description):
    group = commands.add_parser(name, help=description)
    return group.add_subparsers(dest="action", required=True, metavar="ACTION")


def parser():
    result = HelpParser(
        description="Write books in Markdown. Build EPUBs and preview changes live."
    )
    result.add_argument(
        "--version", action="version", version=f"mightier {__version__}"
    )
    result.add_argument(
        "--log-level",
        choices=LEVELS,
        default="debug",
        help="File log level. Defaults to debug.",
    )
    result.add_argument(
        "-P",
        "--project",
        type=Path,
        help="Book folder containing mightier.toml. Defaults to the nearest parent project.",
    ).completer = DirectoriesCompleter()
    commands = result.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("help", help="Show the logo and command help.")
    commands.add_parser(
        "setup", help="Install missing Pandoc and EPUBCheck dependencies with Homebrew."
    )
    initialize = commands.add_parser(
        "init", help="Initialize a book without overwriting existing content."
    )
    initialize.add_argument(
        "directory",
        type=Path,
        nargs="?",
        help="Book folder. Defaults to the current directory.",
    ).completer = DirectoriesCompleter()
    initialize.add_argument(
        "--metadata-dir",
        type=Path,
        help="Shared metadata directory. Relative to the current working directory.",
    ).completer = DirectoriesCompleter()
    commands.add_parser("check", help="Validate the book and its shared metadata.")
    build = commands.add_parser(
        "build", help="Build an EPUB, or a standalone HTML file."
    )
    build.add_argument("--format", choices=("epub", "html"), default="epub")
    preview = commands.add_parser(
        "serve", help="Serve a local HTML preview and reload it on saved changes."
    )
    preview.add_argument("--port", type=int, default=8000).completer = no_values
    preview.add_argument(
        "--open", action="store_true", help="Open the preview in your browser."
    )

    chapters = resource(commands, "chapter", "Create and rename chapter files.")
    new = chapters.add_parser(
        "new", help="Create a chapter and append it to mightier.toml."
    )
    new.add_argument(
        "id", help="Filename stem, such as 02-return."
    ).completer = SuppressCompleter()
    new.add_argument(
        "--title", help="Opening heading. Defaults to text derived from the filename."
    ).completer = no_values
    rename = chapters.add_parser(
        "rename", help="Rename a chapter without changing its text or reading order."
    )
    rename.add_argument(
        "old", help="Listed filename stem or book-relative chapter path."
    ).completer = chapter_names
    rename.add_argument(
        "new", help="New filename stem, such as 02-homecoming."
    ).completer = SuppressCompleter()

    for kind in ENTITY_FOLDERS:
        actions = resource(commands, kind, f"Create, edit, and rename {kind} records.")
        actions.add_parser("new", help=f"Create a {kind} record.").add_argument(
            "id"
        ).completer = SuppressCompleter()
        actions.add_parser("edit", help=f"Edit a {kind} record.").add_argument(
            "id"
        ).completer = record_names(kind)
        rename = actions.add_parser(
            "rename", help=f"Rename a {kind} ID and update its references."
        )
        rename.add_argument("old", help="Existing record ID.").completer = record_names(
            kind
        )
        rename.add_argument(
            "new", help="New record ID."
        ).completer = SuppressCompleter()
        rename.add_argument(
            "--scope",
            type=Path,
            help="Directory containing all affected books and their shared metadata.",
        ).completer = DirectoriesCompleter()
        rename.add_argument(
            "--dry-run",
            action="store_true",
            help="List planned changes without modifying manuscript files.",
        )

    references = resource(
        commands, "reference", "Create and edit bibliography entries."
    )
    references.add_parser("new", help="Add a BibTeX entry.").add_argument(
        "id"
    ).completer = SuppressCompleter()
    references.add_parser("edit", help="Edit a BibTeX entry.").add_argument(
        "id"
    ).completer = record_names("reference")
    resource(commands, "book", "Edit the book configuration.").add_parser(
        "edit", help="Edit mightier.toml."
    )
    commands.add_parser("clean", help="Remove this book's build directory only.")
    commands.add_parser(
        "completion", help="Generate shell completion for Fish, Bash, or Zsh."
    ).add_argument("shell", choices=SHELLS)
    data = commands.add_parser(
        "completion-data", help="Read metadata as JSON for editor integration."
    )
    data.add_argument(
        "--metadata-dir",
        type=Path,
        help="Read a standalone metadata directory without a book project.",
    ).completer = DirectoriesCompleter()
    return result


def main(argv=None):
    command_parser = parser()
    argcomplete.autocomplete(command_parser, always_complete_options=False)
    argv = sys.argv[1:] if argv is None else argv
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument("--log-level", choices=LEVELS, default="debug")
    log_options, _ = preliminary.parse_known_args(argv)
    try:
        configure(log_options.log_level)
    except OSError as error:
        warning(f"Could not open mightier.log in the current folder: {error}")
    logger.debug(
        "CLI started: version=%s cwd=%s argv=%r", __version__, Path.cwd(), argv
    )
    if not argv:
        command_parser.print_help()
        return 0
    arguments = command_parser.parse_args(argv)
    try:
        if arguments.command == "help":
            command_parser.print_help()
            return 0
        if arguments.command == "completion":
            sys.stdout.write(script(arguments.shell))
            return 0
        if arguments.command == "setup":
            setup()
            return 0
        if arguments.command == "init":
            if arguments.project is not None and arguments.directory is not None:
                raise BookError(
                    "Choose either --project or an init directory, not both."
                )
            Project(arguments.directory or arguments.project or Path.cwd()).initialize(
                arguments.metadata_dir
            )
            return 0
        if (
            arguments.command == "completion-data"
            and arguments.metadata_dir is not None
        ):
            if arguments.project is not None:
                raise BookError(
                    "Choose either --project or --metadata-dir for completion-data."
                )
            print(
                json.dumps(
                    collect(Metadata(arguments.metadata_dir)), ensure_ascii=False
                )
            )
            return 0
        project = (
            Project(arguments.project)
            if arguments.project is not None
            else Project.discover()
        )
        if arguments.command == "completion-data":
            print(json.dumps(collect(project), ensure_ascii=False))
        elif arguments.command == "check":
            project.check()
        elif arguments.command == "build":
            with console.status(f"Building {arguments.format.upper()}..."):
                project.build(arguments.format)
        elif arguments.command == "serve":
            from mightier.preview import serve

            serve(project, port=arguments.port, open_browser=arguments.open)
        elif arguments.command == "clean":
            project.clean()
        elif arguments.command == "chapter":
            from mightier.chapters import new_chapter, rename_chapter

            if arguments.action == "new":
                new_chapter(project, arguments.id, arguments.title)
            else:
                rename_chapter(project, arguments.old, arguments.new)
        elif arguments.command in ENTITY_FOLDERS:
            if arguments.action == "new":
                project.new_entity(arguments.command, arguments.id)
            elif arguments.action == "edit":
                from mightier.editing import edit

                edit(project, arguments.command, arguments.id)
            else:
                from mightier.renaming import rename_entity

                rename_entity(
                    project,
                    arguments.command,
                    arguments.old,
                    arguments.new,
                    arguments.scope,
                    arguments.dry_run,
                )
        elif arguments.command == "reference" and arguments.action == "new":
            project.new_reference(arguments.id)
        else:
            from mightier.editing import edit

            edit(project, arguments.command, getattr(arguments, "id", None))
    except (BookError, OSError, UnicodeError) as error:
        logger.exception(
            "Command failed: %s %s", arguments.command, getattr(arguments, "action", "")
        )
        failure(str(error))
        return 1
    except KeyboardInterrupt:
        logger.info("Command interrupted")
        console.print("Stopped.", style="dim")
    logger.debug("CLI finished successfully")
    return 0

"""The Mightier command-line interface."""

import argparse
import json
import sys
from pathlib import Path

from mightier import __version__
from mightier.books import ENTITY_FOLDERS, BookError, Project
from mightier.completion import collect
from mightier.logging_config import LEVELS, configure, logger
from mightier.output import console, failure, warning
from mightier.setup import setup


class HelpParser(argparse.ArgumentParser):
    def print_help(self, file=None):
        from mightier.logo import help_header

        if file is None or file is sys.stdout:
            help_header()
            console.print(self.format_help(), markup=False, end="")
        else:
            super().print_help(file)


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
    )
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
    )
    initialize.add_argument(
        "--metadata-dir",
        type=Path,
        help="Shared metadata directory. Relative to the current working directory.",
    )
    commands.add_parser("check", help="Validate the book and its shared metadata.")
    build = commands.add_parser(
        "build", help="Build an EPUB, or a standalone HTML file."
    )
    build.add_argument("--format", choices=("epub", "html"), default="epub")
    preview = commands.add_parser(
        "serve", help="Serve a local HTML preview and reload it on saved changes."
    )
    preview.add_argument("--port", type=int, default=8000)
    preview.add_argument(
        "--open", action="store_true", help="Open the preview in your browser."
    )
    new = commands.add_parser(
        "new", help="Create a chapter, metadata record, or reference."
    )
    kinds = new.add_subparsers(dest="kind", required=True, metavar="TYPE")
    chapter = kinds.add_parser(
        "chapter", help="Create a chapter and append it to mightier.toml."
    )
    chapter.add_argument("id", help="Filename stem, such as 02-return.")
    chapter.add_argument(
        "--title", help="Opening heading. Defaults to text derived from the filename."
    )
    for kind in (*ENTITY_FOLDERS, "reference"):
        kinds.add_parser(kind, help=f"Create a {kind} record.").add_argument("id")
    rename = commands.add_parser(
        "rename", help="Rename a file and update its configuration."
    )
    renamable = rename.add_subparsers(dest="kind", required=True, metavar="TYPE")
    renamed_chapter = renamable.add_parser(
        "chapter", help="Rename a chapter without changing its text or reading order."
    )
    renamed_chapter.add_argument(
        "old", help="Listed filename stem or book-relative chapter path."
    )
    renamed_chapter.add_argument(
        "new", help="New filename stem, such as 02-homecoming."
    )
    edit = commands.add_parser(
        "edit", help="Open and validate an individual record or book configuration."
    )
    editable = edit.add_subparsers(dest="kind", required=True, metavar="TYPE")
    editable.add_parser("book", help="Edit mightier.toml.")
    for kind in (*ENTITY_FOLDERS, "reference"):
        editable.add_parser(kind, help=f"Edit a {kind} record.").add_argument("id")
    commands.add_parser("clean", help="Remove this book's build directory only.")
    commands.add_parser(
        "completion-data", help="Read metadata as JSON for editor integration."
    )
    return result


def main(argv=None):
    command_parser = parser()
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
        if arguments.command == "setup":
            setup()
            return 0
        if arguments.command == "init":
            if arguments.project is not None and arguments.directory is not None:
                raise BookError(
                    "Choose either --project or an init directory, not both."
                )
            project = Project(arguments.directory or arguments.project or Path.cwd())
            project.initialize(arguments.metadata_dir)
            return 0
        project = (
            Project(arguments.project)
            if arguments.project is not None
            else Project.discover()
        )
        if arguments.command == "completion-data":
            # Keep stdout valid JSON even when terminal colours are forced.
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
        elif arguments.command == "edit":
            from mightier.editing import edit

            edit(project, arguments.kind, getattr(arguments, "id", None))
        elif arguments.command == "rename":
            from mightier.chapters import rename_chapter

            rename_chapter(project, arguments.old, arguments.new)
        elif arguments.command == "new":
            if arguments.kind == "chapter":
                from mightier.chapters import new_chapter

                new_chapter(project, arguments.id, arguments.title)
            elif arguments.kind == "reference":
                project.new_reference(arguments.id)
            else:
                project.new_entity(arguments.kind, arguments.id)
    except (BookError, OSError, UnicodeError) as error:
        logger.exception("Command failed: %s", arguments.command)
        failure(str(error))
        return 1
    except KeyboardInterrupt:
        logger.info("Command interrupted")
        console.print("Stopped.", style="dim")
    logger.debug("CLI finished successfully")
    return 0

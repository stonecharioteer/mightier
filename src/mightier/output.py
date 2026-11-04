"""Rich output for people. Machine-readable commands do not use this module."""

from rich.console import Console

from mightier.logging_config import logger

console = Console(highlight=False)
error_console = Console(stderr=True, highlight=False)


def success(message):
    logger.info(message)
    console.print(message, style="green", markup=False)


def warning(message):
    logger.warning(message)
    error_console.print(message, style="yellow", markup=False)


def failure(message):
    logger.error(message)
    error_console.print(f"Error: {message}", style="red", markup=False)

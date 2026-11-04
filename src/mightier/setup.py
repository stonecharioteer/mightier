"""Install missing external build tools. Python and uv are bootstrap tools."""

import shutil
import subprocess

from rich.table import Table

from mightier.books import BookError
from mightier.output import console, success

TOOLS = {"pandoc": "pandoc", "epubcheck": "epubcheck"}


def setup():
    table = Table(title="Mightier dependencies", box=None, padding=(0, 2))
    table.add_column("Tool", style="bold")
    table.add_column("Status", style="green")
    for command, formula in TOOLS.items():
        if shutil.which(command):
            table.add_row(command, "Available")
            continue
        if not shutil.which("brew"):
            raise BookError(
                f"Cannot install {command}. Install Homebrew from https://brew.sh first."
            )
        console.print(
            f"Installing {formula} with Homebrew.", style="cyan", markup=False
        )
        result = subprocess.run(["brew", "install", formula], check=False)
        if result.returncode:
            raise BookError(f"Homebrew could not install {formula}. Setup stopped.")
        table.add_row(command, "Installed")
    console.print(table)
    success("Setup complete. Existing tools were not upgraded.")

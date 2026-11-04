"""Display the same logo as pixels, or as a terminal image when supported."""

import base64
import json
import os
from importlib.resources import files

from rich.style import Style
from rich.text import Text

from mightier.output import console

ASSETS = files("mightier").joinpath("assets")


def image_sequence(encoded, environment):
    """Use native protocols only in terminals that explicitly identify support."""
    if environment.get("TMUX") or environment.get("STY") or "NO_COLOR" in environment:
        return None
    term = environment.get("TERM", "")
    program = environment.get("TERM_PROGRAM", "").lower()
    if (
        environment.get("KITTY_WINDOW_ID")
        or term in ("xterm-kitty", "xterm-ghostty")
        or program == "ghostty"
    ):
        chunks = [
            encoded[index : index + 4096] for index in range(0, len(encoded), 4096)
        ]
        output = []
        for index, chunk in enumerate(chunks):
            more = int(index < len(chunks) - 1)
            options = f"a=T,f=100,t=d,c=16,r=8,m={more}" if index == 0 else f"m={more}"
            output.append(f"\033_G{options};{chunk}\033\\")
        return "".join(output) + "\n"
    if program == "iterm.app":
        return f"\033]1337;File=inline=1;width=16;height=8;preserveAspectRatio=1:{encoded}\a\n"
    return None


def block_logo():
    data = json.loads(ASSETS.joinpath("logo-pixels.json").read_text(encoding="utf-8"))
    palette, rows = data["palette"], data["rows"]
    text = Text()
    for index in range(0, len(rows), 2):
        for upper, lower in zip(rows[index], rows[index + 1]):
            top, bottom = palette[int(upper, 16)], palette[int(lower, 16)]
            if top:
                text.append("▀", Style(color=top, bgcolor=bottom or None))
            elif bottom:
                text.append("▄", Style(color=bottom))
            else:
                text.append(" ")
        text.append("\n")
    return text


def help_header():
    stream = console.file
    sequence = None
    if getattr(stream, "isatty", lambda: False)():
        encoded = base64.b64encode(ASSETS.joinpath("mightier.png").read_bytes()).decode(
            "ascii"
        )
        sequence = image_sequence(encoded, os.environ)
    if sequence:
        stream.write(sequence)
        stream.flush()
    else:
        console.print(block_logo(), end="")
    console.print("Mightier", style="bold cyan")
    quote = Text("The Pen is ")
    quote.append("mightier", style="italic")
    quote.append(" than the Sword.")
    console.print(quote)
    console.print()

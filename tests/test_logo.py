import unittest
import xml.etree.ElementTree as ET

from mightier.logo import ASSETS, block_logo, image_sequence


class LogoTests(unittest.TestCase):
    def test_svg_png_and_terminal_pixels_are_bundled(self):
        svg = ET.fromstring(ASSETS.joinpath("mightier.svg").read_bytes())
        self.assertEqual(svg.attrib["shape-rendering"], "crispEdges")
        self.assertTrue(
            ASSETS.joinpath("mightier.png")
            .read_bytes()
            .startswith(b"\x89PNG\r\n\x1a\n")
        )
        self.assertIn("▀", block_logo().plain)

    def test_kitty_and_ghostty_protocols_are_chunked(self):
        for environment in (
            {"TERM": "xterm-kitty"},
            {"TERM_PROGRAM": "ghostty"},
            {"TERM": "xterm-ghostty"},
        ):
            sequence = image_sequence("x" * 5000, environment)
            self.assertIn("\033_Ga=T", sequence)
            self.assertIn("m=1", sequence)
            self.assertIn("m=0", sequence)

    def test_iterm_and_unsupported_terminals(self):
        self.assertIn(
            "1337;File=", image_sequence("data", {"TERM_PROGRAM": "iTerm.app"})
        )
        self.assertIsNone(image_sequence("data", {"TERM": "xterm-256color"}))
        self.assertIsNone(
            image_sequence("data", {"TERM_PROGRAM": "ghostty", "TMUX": "session"})
        )
        self.assertIsNone(
            image_sequence("data", {"TERM_PROGRAM": "ghostty", "NO_COLOR": "1"})
        )

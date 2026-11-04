import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ClientTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("node"), "Node is required for JavaScript client tests"
    )
    def test_reload_scroll_restore_errors_and_reconnection(self):
        result = subprocess.run(
            [
                "node",
                str(ROOT / "tests/preview_client.cjs"),
                str(ROOT / "src/mightier/assets/preview.js"),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Live reload client checks passed", result.stdout)

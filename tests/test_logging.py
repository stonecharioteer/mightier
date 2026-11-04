"""The active log and one backup must stay below 10 MB in total."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mightier import logging_config as logs


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(logs.configure, "off")

    def test_defaults_and_private_permissions(self):
        logs.configure(directory=self.root)
        logs.logger.debug("Detailed diagnostics")
        handler = logs.logger.handlers[0]
        self.assertEqual(handler.maxBytes, 5_000_000)
        self.assertEqual(handler.backupCount, 1)
        path = self.root / "mightier.log"
        self.assertIn("Detailed diagnostics", path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rotates_by_utf8_bytes_and_bounds_large_records(self):
        with (
            patch.object(logs, "FILE_BYTES", 1024),
            patch.object(logs, "RECORD_BYTES", 512),
        ):
            logs.configure(directory=self.root)
            for _ in range(30):
                logs.logger.debug("龍" * 1000)
        paths = sorted(self.root.glob("mightier.log*"))
        self.assertEqual(
            [path.name for path in paths], ["mightier.log", "mightier.log.1"]
        )
        self.assertLessEqual(sum(path.stat().st_size for path in paths), 2048)
        self.assertIn("truncated", paths[0].read_text())

    def test_concurrent_processes_share_rotation_safely(self):
        script = (
            "import sys; from mightier import logging_config as logs; "
            "logs.FILE_BYTES = 1024; logs.RECORD_BYTES = 512; "
            "logs.configure(directory=sys.argv[1]); "
            "[logs.logger.debug('並' * 200) for _ in range(50)]"
        )
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(self.root)], stderr=subprocess.PIPE
            )
            for _ in range(3)
        ]
        for process in processes:
            _, errors = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, errors)
            self.assertEqual(errors, b"")
        paths = list(self.root.glob("mightier.log*"))
        self.assertEqual(len(paths), 2)
        self.assertLessEqual(sum(path.stat().st_size for path in paths), 2048)

    def test_level_changes_and_off(self):
        logs.configure("warning", directory=self.root)
        logs.logger.debug("Do not include")
        logs.logger.warning("Include this")
        path = self.root / "mightier.log"
        self.assertNotIn("Do not include", path.read_text())
        self.assertIn("Include this", path.read_text())
        before = path.read_bytes()
        logs.configure("off", directory=self.root)
        logs.logger.error("Disabled")
        self.assertEqual(path.read_bytes(), before)

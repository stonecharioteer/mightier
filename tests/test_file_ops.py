"""Protect on-disk content when a write fails or another writer intervenes."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mightier.books import BookError
from mightier.file_ops import atomic_write, create_file


class FileOperationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "record.md"

    def test_exclusive_creation_never_overwrites(self):
        self.path.write_bytes(b"Existing content")
        with self.assertRaisesRegex(BookError, "already exists"):
            create_file(self.path, b"Replacement")
        self.assertEqual(self.path.read_bytes(), b"Existing content")

    def test_failed_creation_removes_an_unchanged_copy(self):
        with (
            patch("mightier.file_ops.os.fsync", side_effect=OSError("Disk failure")),
            self.assertRaises(OSError),
        ):
            create_file(self.path, b"New content")
        self.assertFalse(self.path.exists())

    def test_failed_creation_keeps_a_concurrent_edit(self):
        def concurrent(_descriptor):
            self.path.write_bytes(b"User content")
            raise OSError("Disk failure")

        with (
            patch("mightier.file_ops.os.fsync", side_effect=concurrent),
            self.assertRaises(OSError),
        ):
            create_file(self.path, b"New content")
        self.assertEqual(self.path.read_bytes(), b"User content")

    def test_private_permissions_are_set_before_content_is_flushed(self):
        modes = []
        with patch(
            "mightier.file_ops.os.fsync",
            side_effect=lambda fd: modes.append(os.fstat(fd).st_mode & 0o777),
        ):
            create_file(self.path, b"Private notes", mode=0o600)
        self.assertEqual(modes, [0o600])

    def test_atomic_replacement_keeps_permission_bits(self):
        self.path.write_bytes(b"Old\r\n")
        self.path.chmod(0o600)
        atomic_write(self.path, b"Old\r\n", b"New\r\n")
        self.assertEqual(self.path.read_bytes(), b"New\r\n")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_atomic_replacement_refuses_a_concurrent_edit(self):
        self.path.write_bytes(b"Old")

        def concurrent(_descriptor):
            self.path.write_bytes(b"User edit")

        with (
            patch("mightier.file_ops.os.fsync", side_effect=concurrent),
            self.assertRaisesRegex(BookError, "concurrent edit was kept"),
        ):
            atomic_write(self.path, b"Old", b"New")
        self.assertEqual(self.path.read_bytes(), b"User edit")
        self.assertFalse(list(self.path.parent.glob(".mightier-*")))

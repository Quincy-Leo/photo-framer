# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks for cache ownership, session isolation and output allocation.

Run: python3 tests/test_modules.py
"""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

import framer
import server


class ModuleBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="framer-modules-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.photo = self.root / "photo.png"
        Image.new("RGB", (180, 120), (40, 80, 120)).save(self.photo)

    def wait_for_job(self, registry, job):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            report = registry.report(job["id"])
            if report["finished"]:
                return report
            threading.Event().wait(0.01)
        self.fail("Batch job did not finish")

    def test_applications_do_not_share_sessions_jobs_or_uploads(self):
        first = server.ServerApplication(str(self.root / "first"))
        second = server.ServerApplication(str(self.root / "second"))
        self.assertNotEqual(first.token, second.token)
        session = first.sessions.open(self.photo)
        with self.assertRaises(KeyError):
            second.sessions.get(session["id"])
        job = first.jobs.start(str(self.root / "missing"), framer.Params(), "png", None, {})
        self.assertIsNone(second.jobs.report(job["id"]))
        staged = first.cache.stage(b"photo bytes", "../photo.png")
        self.assertEqual(Path(staged).parent, self.root / "first" / ".tmp")
        self.assertEqual(Path(staged).read_bytes(), b"photo bytes")
        self.assertFalse((self.root / "second" / ".tmp").exists())

    def test_sessions_evict_the_least_recently_used_photo(self):
        sessions = server.SessionStore(limit=2)
        first = sessions.open(self.photo)
        second = sessions.open(self.photo)
        sessions.get(first["id"])
        third = sessions.open(self.photo)
        self.assertIs(sessions.get(first["id"]), first)
        self.assertIs(sessions.get(third["id"]), third)
        with self.assertRaises(KeyError):
            sessions.get(second["id"])

    def test_upload_sweep_preserves_recent_uploads_and_other_files(self):
        cache = server.UploadCache(str(self.root / "uploads"))
        old = cache.stage(b"old", "old.png")
        recent = cache.stage(b"recent", "recent.png")
        other = Path(cache.directory) / "kept.png"
        other.write_bytes(b"original")
        expired = time.time() - 100
        os.utime(old, (expired, expired))
        os.utime(other, (expired, expired))
        cache.sweep(max_age=10)
        self.assertFalse(Path(old).exists())
        self.assertEqual(Path(recent).read_bytes(), b"recent")
        self.assertEqual(other.read_bytes(), b"original")

    def test_batch_scan_excludes_hidden_files_subdirectories_and_outputs(self):
        shutil.copyfile(self.photo, self.root / "second.PNG")
        shutil.copyfile(self.photo, self.root / ".hidden.png")
        (self.root / "notes.txt").write_text("not a photo")
        (self.root / "framed").mkdir()
        shutil.copyfile(self.photo, self.root / "framed" / "already.png")
        paths = server.JobRegistry.list_images(str(self.root))
        self.assertEqual([Path(path).name for path in paths], ["photo.png", "second.PNG"])

    def test_batch_cancellation_finishes_the_current_photo_only(self):
        shutil.copyfile(self.photo, self.root / "second.png")
        shutil.copyfile(self.photo, self.root / "third.png")
        entered, release = threading.Event(), threading.Event()
        registry = server.JobRegistry()

        def render(path, params, fmt, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Test render was not released")
            return str(self.root / "framed" / Path(path).name), (180, 120)

        with patch.object(framer.PhotoFramer, "render_file", side_effect=render):
            job = registry.start(str(self.root), framer.Params(), "png", None, {})
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(registry.cancel(job["id"]))
            finally:
                release.set()
            report = self.wait_for_job(registry, job)
        self.assertEqual(report["done"], 1)
        self.assertEqual(len(report["written"]), 1)
        self.assertEqual(report["errors"], [])
        self.assertNotIn("cancel", report)

    def test_repeated_exports_keep_existing_files(self):
        params = framer.Params(style="bar")
        first, size = framer.PhotoFramer.render_file(self.photo, params)
        original = Path(first).read_bytes()
        second, second_size = framer.PhotoFramer.render_file(self.photo, params)
        self.assertEqual(Path(first).name, "photo_framed.png")
        self.assertEqual(Path(second).name, "photo_framed-2.png")
        self.assertEqual(Path(first).read_bytes(), original)
        self.assertEqual(Path(second).read_bytes(), original)
        self.assertEqual(size, second_size)

    def test_bundled_fonts_remain_available_outside_macos(self):
        with patch.object(sys, "platform", "linux"):
            fonts = framer.FontBook()
        self.assertEqual(fonts.faces, fonts.OPEN)
        self.assertTrue(Path(fonts.faces.sans).is_file())
        self.assertTrue(Path(fonts.faces.cjk).is_file())
        latin, chinese = fonts.sans(40, "Semibold"), fonts.cjk(40)
        self.assertEqual(latin.size, 40)
        self.assertEqual(chinese.size, 40)
        self.assertFalse(fonts.covers(latin, "摄"))
        self.assertTrue(fonts.covers(chinese, "摄"))


if __name__ == "__main__":
    unittest.main()

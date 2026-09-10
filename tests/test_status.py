"""The status file the daemon publishes and the tray reads.

Deliberately a plain file rather than an IPC protocol, so these tests are
just as plain: they check atomicity, staleness and that a reader never sees
a half-written record.
"""

import json
import os
import shutil
import tempfile
import time
import unittest

from montech_hyperflow import status as S


class Writing(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "status.json")

    def test_writes_and_reads_back(self):
        S.StatusWriter(self.path).write(celsius=42, unit="C")
        record = S.read(self.path)
        self.assertEqual(record["celsius"], 42)
        self.assertEqual(record["unit"], "C")
        self.assertFalse(record["stale"])
        self.assertLess(record["age"], 5)

    def test_stamps_updated(self):
        before = time.time()
        S.StatusWriter(self.path).write(celsius=1)
        self.assertGreaterEqual(S.read(self.path)["updated"], before)

    def test_leaves_no_temp_file(self):
        writer = S.StatusWriter(self.path)
        for i in range(5):
            writer.write(celsius=i)
        self.assertEqual([f for f in os.listdir(self.dir) if ".tmp" in f], [])

    def test_is_world_readable(self):
        # the daemon runs as a DynamicUser; the tray runs as the desktop user
        S.StatusWriter(self.path).write(celsius=42)
        self.assertTrue(os.stat(self.path).st_mode & 0o044)

    def test_clear_removes_it(self):
        writer = S.StatusWriter(self.path)
        writer.write(celsius=42)
        writer.clear()
        self.assertIsNone(S.read(self.path))

    def test_clear_is_safe_when_absent(self):
        S.StatusWriter(self.path).clear()          # must not raise

    def test_write_never_raises_on_a_bad_path(self):
        writer = S.StatusWriter("/proc/definitely/not/writable/status.json")
        writer.write(celsius=42)                   # must not raise


class Reading(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "status.json")

    def test_missing_file_is_none(self):
        self.assertIsNone(S.read(self.path))

    def test_corrupt_json_is_none_not_an_exception(self):
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertIsNone(S.read(self.path))

    def test_non_object_json_is_rejected(self):
        with open(self.path, "w") as fh:
            json.dump([1, 2, 3], fh)
        self.assertIsNone(S.read(self.path))

    def test_old_record_is_flagged_stale_not_hidden(self):
        # the UI should be able to say "last seen 4 minutes ago" rather than
        # silently showing nothing
        with open(self.path, "w") as fh:
            json.dump({"celsius": 42,
                       "updated": time.time() - S.STALE_AFTER - 60}, fh)
        record = S.read(self.path)
        self.assertIsNotNone(record)
        self.assertTrue(record["stale"])
        self.assertGreater(record["age"], S.STALE_AFTER)

    def test_record_with_no_timestamp_is_stale(self):
        with open(self.path, "w") as fh:
            json.dump({"celsius": 42}, fh)
        self.assertTrue(S.read(self.path)["stale"])

    def test_search_paths_include_the_runtime_fallback(self):
        paths = S.search_paths()
        self.assertEqual(paths[0], S.STATUS_PATH)
        if os.environ.get("XDG_RUNTIME_DIR"):
            self.assertTrue(any("montech-hyperflow" in p for p in paths[1:]))


class TrayServiceLayer(unittest.TestCase):
    """tray.service must not drag in GTK -- it is imported on headless CI."""

    def test_imports_without_a_display(self):
        from montech_hyperflow.tray import service
        self.assertTrue(hasattr(service, "read_status"))
        self.assertEqual(service.UNIT, "montech-hyperflow.service")

    def test_no_gtk_in_the_service_layer(self):
        import inspect

        from montech_hyperflow.tray import service
        source = inspect.getsource(service)
        self.assertNotIn("import gi", source)
        self.assertNotIn("Gtk", source)


class ExplicitPathIsAuthoritative(unittest.TestCase):
    """read(path) must never answer from a different daemon's status file.

    Regression: search_paths() used to append the XDG_RUNTIME_DIR fallback
    even when the caller named a path, so read("/tmp/absent") could return a
    live record from /run/user/N/montech-hyperflow/status.json.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_named_path_is_the_only_path_searched(self):
        self.assertEqual(S.search_paths("/tmp/somewhere/status.json"),
                         ["/tmp/somewhere/status.json"])

    def test_absent_named_path_is_none_even_with_a_live_fallback(self):
        fallback = S._fallback_dir()
        if fallback:
            os.makedirs(fallback, exist_ok=True)
            live = os.path.join(fallback, "status.json")
            with open(live, "w") as fh:
                json.dump({"celsius": 99, "updated": time.time()}, fh)
            self.addCleanup(lambda: os.path.exists(live) and os.unlink(live))
        self.assertIsNone(S.read(os.path.join(self.dir, "absent.json")))

    def test_default_still_searches_the_fallback(self):
        paths = S.search_paths()
        self.assertEqual(paths[0], S.STATUS_PATH)
        if S._fallback_dir():
            self.assertEqual(len(paths), 2)


class MenuSelection(unittest.TestCase):
    """Which unit/source the tray ticks.

    Regression: with no status published the menu fell back to its built-in
    defaults, so after switching to Fahrenheit the head showed degF while the
    menu still ticked Celsius. Status is the truth about what is displayed;
    config is the truth about what was chosen.
    """

    def setUp(self):
        from montech_hyperflow.tray import service
        self.service = service

    def test_live_status_wins(self):
        record = {"unit": "F", "source": "gpu", "stale": False}
        self.assertEqual(self.service.selected_unit(record, {}), "F")
        self.assertEqual(self.service.selected_source(record, {}), "gpu")

    def test_config_used_when_no_status(self):
        settings = {"fahrenheit": True, "source": "gpu"}
        self.assertEqual(self.service.selected_unit(None, settings), "F")
        self.assertEqual(self.service.selected_source(None, settings), "gpu")

    def test_config_used_when_status_is_stale(self):
        stale = {"unit": "C", "source": "cpu", "stale": True}
        settings = {"fahrenheit": True, "source": "gpu"}
        self.assertEqual(self.service.selected_unit(stale, settings), "F")
        self.assertEqual(self.service.selected_source(stale, settings), "gpu")

    def test_defaults_when_neither_is_available(self):
        self.assertEqual(self.service.selected_unit(None, {}), "C")
        self.assertEqual(self.service.selected_source(None, {}), "cpu")

    def test_explicit_celsius_in_config_is_honoured(self):
        settings = {"fahrenheit": False, "source": "cpu"}
        self.assertEqual(self.service.selected_unit(None, settings), "C")

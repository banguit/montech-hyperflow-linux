"""Config file parsing, validation, precedence, and the privileged writer."""

import os
import tempfile
import unittest

from montech_hyperflow import admin
from montech_hyperflow import config as C
from montech_hyperflow.cli import build_parser


class Loading(unittest.TestCase):

    def _write(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_reads_values(self):
        path = self._write("[montech-hyperflow]\n"
                           "source = gpu\ninterval = 2.5\nfahrenheit = yes\n")
        values, used = C.load(path)
        self.assertEqual(used, path)
        self.assertEqual(values,
                         {"source": "gpu", "interval": 2.5, "fahrenheit": True})

    def test_dashes_normalise_to_underscores(self):
        path = self._write("[montech-hyperflow]\ngpu-index = 1\n")
        self.assertEqual(C.load(path)[0], {"gpu_index": 1})

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(C.load("/nonexistent/montech.conf"), ({}, None))

    def test_unknown_key_is_an_error(self):
        path = self._write("[montech-hyperflow]\nnonsense = 1\n")
        with self.assertRaises(ValueError):
            C.load(path)

    def test_bad_type_is_an_error(self):
        path = self._write("[montech-hyperflow]\ninterval = soon\n")
        with self.assertRaises(ValueError):
            C.load(path)

    def test_missing_section_is_an_error(self):
        path = self._write("[other]\nsource = cpu\n")
        with self.assertRaises(ValueError):
            C.load(path)


class Precedence(unittest.TestCase):

    def test_cli_beats_config(self):
        parser = build_parser()
        parser.set_defaults(interval=9.0, source="gpu")
        args = parser.parse_args(["--interval", "3"])
        self.assertEqual(args.interval, 3.0)      # CLI wins
        self.assertEqual(args.source, "gpu")      # config fills the rest


class Saving(unittest.TestCase):

    def _tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        return os.path.join(d, "montech-hyperflow.conf")

    def test_round_trips(self):
        path = self._tmp()
        C.save(path, {"source": "gpu", "fahrenheit": True, "interval": 2.0})
        values, _ = C.load(path)
        self.assertEqual(values,
                         {"source": "gpu", "fahrenheit": True, "interval": 2.0})

    def test_rejects_unknown_keys(self):
        with self.assertRaises(ValueError):
            C.save(self._tmp(), {"rm_rf": "/"})

    def test_write_is_atomic_no_tmp_left_behind(self):
        path = self._tmp()
        C.save(path, {"source": "cpu"})
        leftovers = [f for f in os.listdir(os.path.dirname(path))
                     if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class AdminHelper(unittest.TestCase):
    """The pkexec'd helper must not be steerable into writing anything else."""

    def test_rejects_unknown_setting(self):
        self.assertEqual(admin.main(["set", "evil=1"]), 2)

    def test_rejects_malformed_argument(self):
        self.assertEqual(admin.main(["set", "nonsense"]), 2)

    def test_rejects_bad_value_type(self):
        self.assertEqual(admin.main(["set", "interval=soon"]), 2)

    def test_requires_the_set_verb(self):
        self.assertEqual(admin.main([]), 2)
        self.assertEqual(admin.main(["delete", "source=cpu"]), 2)

    def test_takes_no_path_argument(self):
        # There is deliberately no way for the caller to choose the target
        # file; the helper only ever writes config.SYSTEM_CONFIG.
        import inspect
        self.assertNotIn("path", inspect.signature(admin.main).parameters)
        self.assertIn("SYSTEM_CONFIG", inspect.getsource(admin.main))

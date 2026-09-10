#!/usr/bin/env python3
"""Hardware-free tests for the montech-hyperflow frame encoder and helpers.

Run:  python3 -m unittest -v test_montech
      make test
"""

import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "montech_hyperflow", os.path.join(_HERE, "montech-hyperflow.py"))
mh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mh)


class FrameLength(unittest.TestCase):
    """The device declares Report Count 0x3F = 63 data bytes for feature 7."""

    def test_default_is_64_not_65(self):
        self.assertEqual(mh.FEATURE_DATA_LEN, 63)
        self.assertEqual(mh.FRAME_LEN, 64)

    def test_every_builder_honours_frame_len(self):
        for build in (lambda n: mh.build_frame(42, frame_len=n),
                      mh.build_blank_frame, mh.build_init_frame):
            for n in (64, 65, 8):
                self.assertEqual(len(build(n)), n)

    def test_ioctl_size_field_tracks_the_buffer(self):
        # HIDIOCSFEATURE(n) must encode n in bits 16..29
        for n in (64, 65):
            self.assertEqual((mh.HIDIOCSFEATURE(n) >> 16) & 0x3FFF, n)
        self.assertEqual(mh.HIDIOCSFEATURE(64), 0xC0404806)
        self.assertEqual(mh.HIDIOCSFEATURE(65), 0xC0414806)


class Digits(unittest.TestCase):

    def test_report_id_and_tail_are_zero(self):
        f = mh.build_frame(47)
        self.assertEqual(f[0], mh.REPORT_ID)
        self.assertEqual(set(f[6:]), {0})

    def test_plain_decimal_splitting(self):
        for celsius, want in ((0, (0, 0, 0)), (7, (0, 0, 7)), (42, (0, 4, 2)),
                              (99, (0, 9, 9)), (100, (1, 0, 0)),
                              (123, (1, 2, 3)), (199, (1, 9, 9))):
            f = mh.build_frame(celsius)
            self.assertEqual(tuple(f[1:4]), want, celsius)

    def test_no_bcd_no_checksum(self):
        # digits are plain 0..9 bytes, never packed
        for celsius in range(0, 200):
            f = mh.build_frame(celsius)
            for d in f[1:4]:
                self.assertLess(d, 10)

    def test_clamped_at_199(self):
        for celsius in (200, 255, 1000):
            self.assertEqual(tuple(mh.build_frame(celsius)[1:4]), (1, 9, 9))

    def test_negative_is_floored_to_zero(self):
        f = mh.build_frame(-5)
        self.assertEqual(tuple(f[1:4]), (0, 0, 0))
        self.assertEqual(f[4] >> 4, 0)


class Byte4(unittest.TestCase):

    def test_level_is_celsius_over_ten_clamped_at_nine(self):
        for celsius, want in ((0, 0), (9, 0), (10, 1), (55, 5), (89, 8),
                              (90, 9), (99, 9), (150, 9), (255, 9)):
            self.assertEqual(mh.build_frame(celsius)[4] >> 4, want, celsius)

    def test_unit_nibble(self):
        self.assertEqual(mh.build_frame(50, unit=mh.UNIT_C)[4] & 0x0F, 0)
        self.assertEqual(mh.build_frame(50, unit=mh.UNIT_F)[4] & 0x0F, 1)

    def test_level_override_for_experiments(self):
        for level in range(10):
            f = mh.build_frame(45, level=level)
            self.assertEqual(f[4] >> 4, level)
            self.assertEqual(tuple(f[1:4]), (0, 4, 5),
                             "digits must not move when level is swept")


class FahrenheitQuirk(unittest.TestCase):
    """The vendor computes `level` from Celsius BEFORE converting to degF.

    PROTOCOL.md 0x00408dfe-0x00408e3b (level) vs 0x00408e4a (conversion).
    This asymmetry is deliberate and must be preserved.
    """

    def test_digits_are_fahrenheit_but_level_is_celsius(self):
        f = mh.build_frame(50, unit=mh.UNIT_F)
        self.assertEqual(tuple(f[1:4]), (1, 2, 2))      # 50 C = 122 F
        self.assertEqual(f[4] >> 4, 5)                  # level from 50 C, not 122
        self.assertEqual(f[4] & 0x0F, mh.UNIT_F)

    def test_level_matches_celsius_across_the_range(self):
        for celsius in range(0, 100):
            c = mh.build_frame(celsius, unit=mh.UNIT_C)
            f = mh.build_frame(celsius, unit=mh.UNIT_F)
            self.assertEqual(c[4] >> 4, f[4] >> 4, celsius)

    def test_zero_celsius_shows_32_f(self):
        # regression: the previous version special-cased 0 C to display "0",
        # which is not what the vendor does and made a real 0 C reading
        # indistinguishable from a dead sensor
        f = mh.build_frame(0, unit=mh.UNIT_F)
        self.assertEqual(tuple(f[1:4]), (0, 3, 2))

    def test_fahrenheit_clamp_collapses_above_93c(self):
        # 93 C = 199 F, so everything at or above it pins at 199. Documented
        # so a Phase 2 oracle report is not misread as a driver bug.
        self.assertEqual(tuple(mh.build_frame(92, unit=mh.UNIT_F)[1:4]),
                         (1, 9, 7))
        for celsius in (93, 100, 150):
            self.assertEqual(tuple(mh.build_frame(celsius, unit=mh.UNIT_F)[1:4]),
                             (1, 9, 9), celsius)


class Byte5(unittest.TestCase):

    def test_source(self):
        self.assertEqual(mh.build_frame(40, source=mh.SRC_CPU)[5], 0)
        self.assertEqual(mh.build_frame(40, source=mh.SRC_GPU)[5], 1)


class Commands(unittest.TestCase):

    def test_blank_is_all_zero_after_the_report_id(self):
        f = mh.build_blank_frame()
        self.assertEqual(f[0], mh.REPORT_ID)
        self.assertEqual(set(f[1:]), {0})

    def test_init_is_fd_in_byte_1(self):
        f = mh.build_init_frame()
        self.assertEqual(f[0], mh.REPORT_ID)
        self.assertEqual(f[1], 0xFD)
        self.assertEqual(set(f[2:]), {0})

    def test_a_real_temperature_can_never_collide_with_a_command(self):
        # byte[1] >= 2 is an out-of-band command; the hundreds digit of a
        # clamped 0..199 value is only ever 0 or 1
        for celsius in range(0, 400):
            for unit in (mh.UNIT_C, mh.UNIT_F):
                self.assertLessEqual(mh.build_frame(celsius, unit=unit)[1], 1)


class DescriptorParser(unittest.TestCase):

    VENDOR = bytes([
        0x06, 0x01, 0xFF,        # Usage Page (0xFF01)
        0x09, 0x01,              # Usage (1)
        0xA1, 0x01,              # Collection (Application)
        0x85, 0x07,              #   Report ID (7)
        0x09, 0x03, 0x15, 0x00, 0x26, 0xFF, 0x00,
        0x75, 0x08, 0x95, 0x3F,  #   Report Size 8, Report Count 63
        0xB1, 0x02,              #   Feature
        0x09, 0x04, 0x15, 0x00, 0x26, 0xFF, 0x00,
        0x75, 0x08, 0x95, 0x3F,
        0x91, 0x02,              #   Output
        0xC0,
    ])

    def test_finds_the_vendor_feature_report(self):
        self.assertEqual(mh.vendor_feature_len(self.VENDOR), 63)

    def test_reports_output_instance_too(self):
        got = mh.parse_report_descriptor(self.VENDOR)
        self.assertEqual(got[(0xFF01, 0x01, 7, "output")], 63)

    def test_substring_heuristic_is_unsound(self):
        # a 4-byte Logical Maximum of 0xFF0106FF spells "06 01 FF" in its
        # data bytes; "09 01" is Usage(1), in most descriptors. Neither
        # declares a vendor page. The old driver would have accepted this.
        poison = bytes([
            0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01,
            0x27, 0xFF, 0x06, 0x01, 0xFF,
            0x75, 0x08, 0x95, 0x02, 0x81, 0x02, 0xC0,
        ])
        self.assertIn(b"\x06\x01\xff", poison)
        self.assertIn(b"\x09\x01", poison)
        self.assertIsNone(mh.vendor_feature_len(poison))

    def test_keyboard_descriptor_is_rejected(self):
        keyboard = bytes([
            0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x05, 0x07,
            0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
            0x75, 0x01, 0x95, 0x08, 0x81, 0x02, 0xC0,
        ])
        self.assertIsNone(mh.vendor_feature_len(keyboard))

    def test_push_pop_restores_globals(self):
        d = bytes([
            0x06, 0x01, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x07,
            0x75, 0x08, 0x95, 0x3F,
            0xA4,                                    # Push
            0x75, 0x01, 0x95, 0x08,
            0xB4,                                    # Pop
            0xB1, 0x02, 0xC0,
        ])
        self.assertEqual(mh.vendor_feature_len(d), 63)

    def test_multiple_main_items_accumulate(self):
        d = bytes([
            0x06, 0x01, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x07,
            0x75, 0x08, 0x95, 0x20, 0xB1, 0x02,
            0x75, 0x08, 0x95, 0x1F, 0xB1, 0x02,
            0xC0,
        ])
        self.assertEqual(mh.vendor_feature_len(d), 63)

    def test_long_item_is_skipped(self):
        self.assertEqual(
            mh.vendor_feature_len(bytes([0xFE, 0x02, 0x01, 0xAA, 0xBB])
                                  + self.VENDOR), 63)

    def test_truncated_input_does_not_raise(self):
        for n in range(len(self.VENDOR)):
            mh.parse_report_descriptor(self.VENDOR[:n])
        mh.parse_report_descriptor(b"")


class SortKeys(unittest.TestCase):

    def test_numeric_not_lexicographic(self):
        nodes = ["/sys/class/hidraw/hidraw10", "/sys/class/hidraw/hidraw2",
                 "/sys/class/hidraw/hidraw1"]
        self.assertEqual(
            [n[-7:] for n in sorted(nodes, key=mh._numeric_sort_key)],
            ["hidraw1", "hidraw2", "idraw10"])


class Rounding(unittest.TestCase):

    def test_nearest_vs_truncate(self):
        self.assertEqual(mh._to_celsius(41499, "nearest"), 41)
        self.assertEqual(mh._to_celsius(41500, "nearest"), 42)
        self.assertEqual(mh._to_celsius(41999, "truncate"), 41)
        self.assertEqual(mh._to_celsius(41000, "truncate"), 41)


class Config(unittest.TestCase):

    def _write(self, text):
        import tempfile
        fh = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_reads_values(self):
        path = self._write("[montech-hyperflow]\n"
                           "source = gpu\ninterval = 2.5\nfahrenheit = yes\n")
        values, used = mh.load_config(path)
        self.assertEqual(used, path)
        self.assertEqual(values,
                         {"source": "gpu", "interval": 2.5, "fahrenheit": True})

    def test_dashes_normalise_to_underscores(self):
        path = self._write("[montech-hyperflow]\ngpu-index = 1\n")
        self.assertEqual(mh.load_config(path)[0], {"gpu_index": 1})

    def test_unknown_key_is_an_error(self):
        path = self._write("[montech-hyperflow]\nnonsense = 1\n")
        with self.assertRaises(ValueError):
            mh.load_config(path)

    def test_bad_type_is_an_error(self):
        path = self._write("[montech-hyperflow]\ninterval = soon\n")
        with self.assertRaises(ValueError):
            mh.load_config(path)

    def test_missing_section_is_an_error(self):
        path = self._write("[other]\nsource = cpu\n")
        with self.assertRaises(ValueError):
            mh.load_config(path)

    def test_cli_beats_config(self):
        path = self._write("[montech-hyperflow]\ninterval = 9\nsource = gpu\n")
        ap = mh.build_parser()
        values, _ = mh.load_config(path)
        ap.set_defaults(**values)
        args = ap.parse_args(["--interval", "3"])
        self.assertEqual(args.interval, 3.0)      # CLI wins
        self.assertEqual(args.source, "gpu")      # config fills the rest


class SensorFailurePolicy(unittest.TestCase):
    """A failed read must never be encoded as a real 0 C frame."""

    class _Args:
        rounding = "nearest"
        on_sensor_error = "hold"
        sensor_error_blank_after = 30.0
        test_level = None
        once = True
        interval = 1.0

    class _Flaky(mh.Sensor):
        description = "flaky"

        def __init__(self, values):
            self.values = list(values)

        def read(self):
            return self.values.pop(0) if self.values else None

    def _loop(self, sensor, **overrides):
        args = self._Args()
        for k, v in overrides.items():
            setattr(args, k, v)
        return mh._Loop(args, sensor, None, mh.UNIT_C, mh.SRC_CPU, 64)

    def test_holds_last_good_value(self):
        loop = self._loop(self._Flaky([45000, None, None]))
        self.assertEqual(loop._value(), (45, None))
        self.assertEqual(loop._value(), (45, None))
        self.assertEqual(loop._value(), (45, None))

    def test_blanks_when_there_was_never_a_reading(self):
        celsius, reason = self._loop(self._Flaky([None]))._value()
        self.assertIsNone(celsius)
        self.assertIsNotNone(reason)

    def test_blank_mode_blanks_immediately(self):
        loop = self._loop(self._Flaky([45000, None]), on_sensor_error="blank")
        self.assertEqual(loop._value(), (45, None))
        self.assertIsNone(loop._value()[0])

    def test_hold_expires_into_a_blank(self):
        loop = self._loop(self._Flaky([45000, None, None]),
                          sensor_error_blank_after=0.001)
        loop._value()
        loop._value()
        import time as _t
        _t.sleep(0.01)
        self.assertIsNone(loop._value()[0])

    def test_hold_forever_when_limit_is_zero(self):
        loop = self._loop(self._Flaky([45000] + [None] * 5),
                          sensor_error_blank_after=0)
        loop._value()
        for _ in range(5):
            self.assertEqual(loop._value(), (45, None))

    def test_recovery_resumes_live_values(self):
        loop = self._loop(self._Flaky([45000, None, 60000]))
        loop._value()
        loop._value()
        self.assertEqual(loop._value(), (60, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Frame encoder tests: one per claim in docs/PROTOCOL.md.

Hardware-free. The vendor quirks asserted here are deliberate; a test failing
because someone "fixed" one is the point.
"""

import unittest

from montech_hyperflow import frame as F


class FrameLength(unittest.TestCase):
    """The device declares Report Count 0x3F = 63 data bytes for feature 7."""

    def test_default_is_64_not_65(self):
        self.assertEqual(F.FEATURE_DATA_LEN, 63)
        self.assertEqual(F.FRAME_LEN, 64)

    def test_every_builder_honours_frame_len(self):
        for build in (lambda n: F.build_frame(42, frame_len=n),
                      F.build_blank_frame, F.build_init_frame):
            for n in (64, 65, 8):
                self.assertEqual(len(build(n)), n)


class Digits(unittest.TestCase):

    def test_report_id_and_tail_are_zero(self):
        f = F.build_frame(47)
        self.assertEqual(f[0], F.REPORT_ID)
        self.assertEqual(set(f[6:]), {0})

    def test_plain_decimal_splitting(self):
        for celsius, want in ((0, (0, 0, 0)), (7, (0, 0, 7)), (42, (0, 4, 2)),
                              (99, (0, 9, 9)), (100, (1, 0, 0)),
                              (123, (1, 2, 3)), (199, (1, 9, 9))):
            self.assertEqual(tuple(F.build_frame(celsius)[1:4]), want, celsius)

    def test_123_is_the_hardware_confirmed_frame(self):
        # Observed on a real HyperFlow Digital 240: this frame made the head
        # read "123". Do not change the encoding without re-testing on metal.
        self.assertEqual(F.build_frame(123)[:6],
                         bytes([0x07, 0x01, 0x02, 0x03, 0x90, 0x00]))

    def test_no_bcd_no_checksum(self):
        for celsius in range(0, 200):
            for digit in F.build_frame(celsius)[1:4]:
                self.assertLess(digit, 10)

    def test_clamped_at_199(self):
        for celsius in (200, 255, 1000):
            self.assertEqual(tuple(F.build_frame(celsius)[1:4]), (1, 9, 9))

    def test_negative_is_floored_to_zero(self):
        f = F.build_frame(-5)
        self.assertEqual(tuple(f[1:4]), (0, 0, 0))
        self.assertEqual(f[4] >> 4, 0)


class Byte4(unittest.TestCase):

    def test_level_is_celsius_over_ten_clamped_at_nine(self):
        for celsius, want in ((0, 0), (9, 0), (10, 1), (55, 5), (89, 8),
                              (90, 9), (99, 9), (150, 9), (255, 9)):
            self.assertEqual(F.build_frame(celsius)[4] >> 4, want, celsius)

    def test_unit_nibble(self):
        self.assertEqual(F.build_frame(50, unit=F.UNIT_C)[4] & 0x0F, 0)
        self.assertEqual(F.build_frame(50, unit=F.UNIT_F)[4] & 0x0F, 1)

    def test_level_override_holds_digits_still(self):
        for level in range(10):
            f = F.build_frame(45, level=level)
            self.assertEqual(f[4] >> 4, level)
            self.assertEqual(tuple(f[1:4]), (0, 4, 5),
                             "digits must not move when level is swept")


class FahrenheitQuirk(unittest.TestCase):
    """The vendor computes `level` from Celsius BEFORE converting to degF.

    docs/PROTOCOL.md 0x00408dfe-0x00408e3b (level) vs 0x00408e4a (conversion).

    CONFIRMED ON HARDWARE 2026-09-10: at 50 C the head showed "50 degC" with 5
    bar segments; with --fahrenheit it showed "122 degF" with STILL 5 segments.
    Had level come from the displayed number, 122 would have clamped it to 9
    and lit the whole bar. These assertions guard observed behaviour, not an
    inference. Do not "fix" them.
    """

    def test_the_photographed_frames(self):
        # the exact two frames that were photographed on the head
        self.assertEqual(F.build_frame(50, unit=F.UNIT_C)[:6],
                         bytes([0x07, 0x00, 0x05, 0x00, 0x50, 0x00]))
        self.assertEqual(F.build_frame(50, unit=F.UNIT_F)[:6],
                         bytes([0x07, 0x01, 0x02, 0x02, 0x51, 0x00]))

    def test_digits_are_fahrenheit_but_level_is_celsius(self):
        f = F.build_frame(50, unit=F.UNIT_F)
        self.assertEqual(tuple(f[1:4]), (1, 2, 2))      # 50 C = 122 F
        self.assertEqual(f[4] >> 4, 5)                  # level from 50, not 122
        self.assertEqual(f[4] & 0x0F, F.UNIT_F)

    def test_level_matches_celsius_across_the_range(self):
        for celsius in range(0, 100):
            self.assertEqual(F.build_frame(celsius, unit=F.UNIT_C)[4] >> 4,
                             F.build_frame(celsius, unit=F.UNIT_F)[4] >> 4,
                             celsius)

    def test_zero_celsius_shows_32_f(self):
        # regression: an earlier draft special-cased 0 C to display "0", which
        # the vendor does not do and which made a real 0 C reading
        # indistinguishable from a dead sensor
        self.assertEqual(tuple(F.build_frame(0, unit=F.UNIT_F)[1:4]), (0, 3, 2))

    def test_fahrenheit_clamp_collapses_at_and_above_93c(self):
        # 93 C = 199 F, so everything above pins at 199. Documented so a
        # Phase 2 oracle report is not misread as a driver bug.
        self.assertEqual(tuple(F.build_frame(92, unit=F.UNIT_F)[1:4]), (1, 9, 7))
        for celsius in (93, 100, 150):
            self.assertEqual(tuple(F.build_frame(celsius, unit=F.UNIT_F)[1:4]),
                             (1, 9, 9), celsius)


class Byte5(unittest.TestCase):

    def test_source(self):
        self.assertEqual(F.build_frame(40, source=F.SRC_CPU)[5], 0)
        self.assertEqual(F.build_frame(40, source=F.SRC_GPU)[5], 1)


class Commands(unittest.TestCase):

    def test_blank_is_all_zero_after_the_report_id(self):
        f = F.build_blank_frame()
        self.assertEqual(f[0], F.REPORT_ID)
        self.assertEqual(set(f[1:]), {0})

    def test_init_is_fd_in_byte_1(self):
        f = F.build_init_frame()
        self.assertEqual(f[0], F.REPORT_ID)
        self.assertEqual(f[1], F.CMD_INIT)
        self.assertEqual(set(f[2:]), {0})

    def test_a_temperature_can_never_collide_with_a_command(self):
        # byte[1] >= 2 is an out-of-band command path; the hundreds digit of a
        # clamped 0..199 value is only ever 0 or 1
        for celsius in range(0, 400):
            for unit in (F.UNIT_C, F.UNIT_F):
                self.assertLessEqual(F.build_frame(celsius, unit=unit)[1], 1)


class DisplayedValue(unittest.TestCase):

    def test_matches_the_digits_the_encoder_emits(self):
        for celsius in range(0, 250):
            for unit in (F.UNIT_C, F.UNIT_F):
                shown = F.displayed_value(celsius, unit)
                f = F.build_frame(celsius, unit=unit)
                self.assertEqual((shown // 100 % 10, shown // 10 % 10,
                                  shown % 10), tuple(f[1:4]), (celsius, unit))

"""HID report-descriptor parser and ioctl encoding."""

import unittest

from montech_hyperflow import hid
from montech_hyperflow.device import vendor_feature_len

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


class Ioctl(unittest.TestCase):

    def test_size_field_tracks_the_buffer(self):
        for n in (64, 65):
            self.assertEqual((hid.HIDIOCSFEATURE(n) >> 16) & 0x3FFF, n)

    def test_known_values(self):
        self.assertEqual(hid.HIDIOCSFEATURE(64), 0xC0404806)
        self.assertEqual(hid.HIDIOCSFEATURE(65), 0xC0414806)


class DescriptorParser(unittest.TestCase):

    def test_finds_the_vendor_feature_report(self):
        self.assertEqual(vendor_feature_len(VENDOR), 63)

    def test_reports_the_output_instance_too(self):
        self.assertEqual(
            hid.parse_report_descriptor(VENDOR)[(0xFF01, 0x01, 7, "output")],
            63)

    def test_substring_heuristic_is_unsound(self):
        # A 4-byte Logical Maximum of 0xFF0106FF spells "06 01 FF" in its data
        # bytes; "09 01" is Usage(1), in most descriptors. Neither declares a
        # vendor page. An earlier version of this driver accepted exactly this.
        poison = bytes([
            0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01,
            0x27, 0xFF, 0x06, 0x01, 0xFF,
            0x75, 0x08, 0x95, 0x02, 0x81, 0x02, 0xC0,
        ])
        self.assertIn(b"\x06\x01\xff", poison)
        self.assertIn(b"\x09\x01", poison)
        self.assertIsNone(vendor_feature_len(poison))

    def test_keyboard_descriptor_is_rejected(self):
        keyboard = bytes([
            0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x05, 0x07,
            0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
            0x75, 0x01, 0x95, 0x08, 0x81, 0x02, 0xC0,
        ])
        self.assertIsNone(vendor_feature_len(keyboard))

    def test_push_pop_restores_globals(self):
        d = bytes([
            0x06, 0x01, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x07,
            0x75, 0x08, 0x95, 0x3F,
            0xA4,                                    # Push
            0x75, 0x01, 0x95, 0x08,
            0xB4,                                    # Pop
            0xB1, 0x02, 0xC0,
        ])
        self.assertEqual(vendor_feature_len(d), 63)

    def test_multiple_main_items_accumulate(self):
        d = bytes([
            0x06, 0x01, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x07,
            0x75, 0x08, 0x95, 0x20, 0xB1, 0x02,
            0x75, 0x08, 0x95, 0x1F, 0xB1, 0x02,
            0xC0,
        ])
        self.assertEqual(vendor_feature_len(d), 63)

    def test_long_item_is_skipped(self):
        self.assertEqual(
            vendor_feature_len(bytes([0xFE, 0x02, 0x01, 0xAA, 0xBB]) + VENDOR),
            63)

    def test_truncated_input_never_raises(self):
        for n in range(len(VENDOR)):
            hid.parse_report_descriptor(VENDOR[:n])
        hid.parse_report_descriptor(b"")

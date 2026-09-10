"""The Montech display frame encoder.

Every claim encoded here is backed by docs/PROTOCOL.md, and the deliberate
vendor quirks are preserved on purpose. Read the note on `build_frame` before
changing anything.
"""

REPORT_ID = 0x07

# The device declares Report Size 8 / Report Count 0x3F for feature report 7,
# i.e. 63 data bytes. Plus the report-ID byte that HIDIOCSFEATURE wants in
# buf[0], that is a 64-byte buffer. Confirmed on hardware.
FEATURE_DATA_LEN = 63
FRAME_LEN = FEATURE_DATA_LEN + 1

CMD_INIT = 0xFD          # byte[1] sentinel the vendor app sends at startup
DISPLAY_MAX = 199        # the vendor app clamps the displayed value here
LEVEL_MAX = 9            # the vendor app clamps level to one decimal digit

SRC_CPU = 0
SRC_GPU = 1

UNIT_C = 0
UNIT_F = 1


def build_frame(celsius, unit=UNIT_C, source=SRC_CPU, frame_len=FRAME_LEN,
                level=None):
    """Encode one display frame.

    byte 0    : 0x07  report ID
    byte 1    : hundreds digit of the displayed value
    byte 2    : tens digit
    byte 3    : ones digit
    byte 4    : (level << 4) | unit    level = min(celsius // 10, 9)
    byte 5    : 0 = CPU, 1 = GPU
    6..len-1  : zero

    NOTE: `level` is always derived from the CELSIUS reading even when the
    digits are Fahrenheit. The vendor computes it before the unit conversion
    (PROTOCOL.md, 0x00408dfe-0x00408e3b vs 0x00408e4a). That asymmetry is
    deliberate; do not "fix" it without hardware evidence.

    `level` may be overridden for experiments; it does not otherwise vary
    independently of the temperature.
    """
    celsius = max(0, int(celsius))
    if level is None:
        level = min(celsius // 10, LEVEL_MAX)
    level = max(0, min(int(level), 0x0F))

    shown = int(celsius * 1.8 + 32) if unit == UNIT_F else celsius
    shown = min(shown, DISPLAY_MAX)

    buf = bytearray(frame_len)
    buf[0] = REPORT_ID
    buf[1] = (shown // 100) % 10
    buf[2] = (shown // 10) % 10
    buf[3] = shown % 10
    buf[4] = (level << 4) | (unit & 0x0F)
    buf[5] = source & 0xFF
    return bytes(buf)


def build_blank_frame(frame_len=FRAME_LEN):
    """All-zero payload: the vendor app sends this to blank the display."""
    buf = bytearray(frame_len)
    buf[0] = REPORT_ID
    return bytes(buf)


def build_init_frame(frame_len=FRAME_LEN):
    """Startup command the vendor app emits before streaming temperatures."""
    buf = bytearray(frame_len)
    buf[0] = REPORT_ID
    buf[1] = CMD_INIT
    return bytes(buf)


def displayed_value(celsius, unit=UNIT_C):
    """What the head will actually read, after conversion and the 199 clamp."""
    celsius = max(0, int(celsius))
    shown = int(celsius * 1.8 + 32) if unit == UNIT_F else celsius
    return min(shown, DISPLAY_MAX)


def format_frame(frame, width=8):
    return " ".join("%02X" % b for b in frame[:width]) + " ..."

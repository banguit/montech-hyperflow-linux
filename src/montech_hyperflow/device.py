"""Finding and holding open the pump head's display interface."""

import errno
import glob
import os
import re

from .frame import FEATURE_DATA_LEN, FRAME_LEN, REPORT_ID, build_init_frame
from .hid import parse_report_descriptor, send_feature

VID = 0x1A2C
PID = 0x4E85

VENDOR_USAGE_PAGE = 0xFF01
VENDOR_USAGE = 0x01

STABLE_LINK = "/dev/montech-hyperflow"      # created by the udev rule

# errnos meaning "this fd is no longer a usable path to the device"
_GONE = frozenset((errno.ENODEV, errno.ENXIO, errno.ESHUTDOWN, errno.EBADF,
                   errno.EIO, errno.EPIPE, errno.ENOENT, errno.EPROTO))


class DeviceGone(Exception):
    """The device disappeared, or the fd stopped being usable."""


def numeric_sort_key(node):
    """Numeric ordering, so hidraw2/hwmon2 sort before hidraw10/hwmon10."""
    m = re.search(r"(\d+)$", node)
    return (int(m.group(1)) if m else 1 << 30, node)


def vendor_feature_len(rd):
    """Data-byte count of the vendor display feature report, or None."""
    return parse_report_descriptor(rd).get(
        (VENDOR_USAGE_PAGE, VENDOR_USAGE, REPORT_ID, "feature"))


def read_descriptor(sysdir):
    try:
        with open(os.path.join(sysdir, "report_descriptor"), "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def _stable_link(path):
    """A by-id symlink for this node. hidrawN itself is not stable."""
    real = os.path.realpath(path)
    if os.path.exists(STABLE_LINK) and os.path.realpath(STABLE_LINK) == real:
        return STABLE_LINK
    for link in sorted(glob.glob("/dev/input/by-id/*-hidraw")):
        try:
            if os.path.realpath(link) == real:
                return link
        except OSError:
            continue
    return None


class Candidate:
    def __init__(self, path, sysdir, feature_len, stable):
        self.path = path
        self.sysdir = sysdir
        self.feature_len = feature_len      # data bytes, or None if unknown
        self.stable = stable

    @property
    def frame_len(self):
        return (self.feature_len or FEATURE_DATA_LEN) + 1

    def __repr__(self):
        return "<Candidate %s frame_len=%d>" % (self.path, self.frame_len)


def find_devices(strict=True):
    """Return Candidates for the cooler's display interface.

    `strict` requires the node to actually declare the 0xFF01 display
    collection, which is what keeps us off the device's boot-keyboard
    interface.
    """
    found = []
    nodes = sorted(glob.glob("/sys/class/hidraw/hidraw*"), key=numeric_sort_key)
    for node in nodes:
        sysdir = os.path.join(node, "device")
        try:
            with open(os.path.join(sysdir, "uevent")) as fh:
                text = fh.read()
        except OSError:
            continue
        m = re.search(r"HID_ID=[0-9A-Fa-f]+:0*([0-9A-Fa-f]+):0*([0-9A-Fa-f]+)",
                      text)
        if not m:
            continue
        if int(m.group(1), 16) != VID or int(m.group(2), 16) != PID:
            continue
        path = "/dev/" + os.path.basename(node)
        flen = vendor_feature_len(read_descriptor(sysdir))
        if strict and flen is None:
            continue
        found.append(Candidate(path, sysdir, flen, _stable_link(path)))
    return found


class PumpDisplay:
    """An open handle to the display that can heal itself.

    A USB re-enumeration (replug, resume, hub reset) gives the device a new
    hidrawN. Rediscovering on every reopen is what makes Restart=always
    unnecessary.
    """

    def __init__(self, explicit=None, strict=True, frame_len=None,
                 send_init=True, log=None):
        self.explicit = explicit
        self.strict = strict
        self.forced_frame_len = frame_len
        self.send_init = send_init
        self.log = log or (lambda _msg: None)
        self.fd = None
        self.path = None
        self.frame_len = frame_len or FRAME_LEN

    def _resolve(self):
        if self.explicit:
            base = os.path.basename(os.path.realpath(self.explicit))
            cand_sys = "/sys/class/hidraw/%s/device" % base
            sysdir = cand_sys if os.path.isdir(cand_sys) else None
            flen = vendor_feature_len(read_descriptor(sysdir)) if sysdir else None
            return Candidate(self.explicit, sysdir, flen, None)
        devices = find_devices(strict=self.strict)
        if not devices:
            raise DeviceGone("no Montech HyperFlow Digital found "
                             "(expected USB %04x:%04x)" % (VID, PID))
        if len(devices) > 1:
            self.log("note: %d matching nodes (%s); using %s"
                     % (len(devices), ", ".join(d.path for d in devices),
                        devices[0].path))
        return devices[0]

    def open(self):
        cand = self._resolve()
        try:
            fd = os.open(cand.path, os.O_RDWR)
        except PermissionError:
            raise PermissionError(
                "permission denied on %s. Install the udev rule "
                "(see README) or run as root." % cand.path)
        except OSError as exc:
            raise DeviceGone("cannot open %s: %s" % (cand.path, exc))
        self.fd = fd
        self.path = cand.path
        self.frame_len = self.forced_frame_len or cand.frame_len
        if (cand.feature_len is not None and self.forced_frame_len is None
                and cand.feature_len != FEATURE_DATA_LEN):
            self.log("note: %s declares %d feature data bytes (expected %d); "
                     "using a %d-byte frame"
                     % (cand.path, cand.feature_len, FEATURE_DATA_LEN,
                        self.frame_len))
        if self.send_init:
            try:
                self.send(build_init_frame(self.frame_len))
            except DeviceGone:
                raise
            except OSError as exc:
                self.log("warning: init command failed (%s); continuing" % exc)
        return self

    def send(self, frame):
        if self.fd is None:
            raise DeviceGone("device is not open")
        try:
            send_feature(self.fd, frame)
        except OSError as exc:
            if exc.errno in _GONE:
                self.close()
                raise DeviceGone("write to %s failed: %s" % (self.path, exc))
            raise

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    @property
    def is_open(self):
        return self.fd is not None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False

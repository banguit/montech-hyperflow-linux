#!/usr/bin/env python3
"""
montech-hyperflow - drive the digital display on a Montech HyperFlow Digital
240/360 AIO pump head from Linux.

Protocol reverse-engineered from "HyperFlow Digital setup 1.0.1.5.exe"
(DeviceDriver.exe, TCOMAS OEM stack) and then corrected against the report
descriptor of the real device. See PROTOCOL.md.

USB 1a2c:4e85, vendor HID collection UsagePage 0xFF01 / Usage 0x01,
HID *feature* report, report ID 0x07, 64 bytes on the wire
(1 report-ID byte + 63 data bytes), pushed once per second.

No third-party dependencies: talks to /dev/hidrawN via HIDIOCSFEATURE.
"""

import argparse
import configparser
import errno
import fcntl
import glob
import os
import re
import signal
import subprocess
import sys
import time

__version__ = "0.2.0"

VID = 0x1A2C
PID = 0x4E85

VENDOR_USAGE_PAGE = 0xFF01
VENDOR_USAGE = 0x01

REPORT_ID = 0x07

# The device declares Report Size 8 / Report Count 0x3F for feature report 7,
# i.e. 63 data bytes.  Plus the report-ID byte that HIDIOCSFEATURE wants in
# buf[0], that is a 64-byte buffer.  Used only when the descriptor cannot be
# read; normally the length is taken from the descriptor at open time.
FEATURE_DATA_LEN = 63
FRAME_LEN = FEATURE_DATA_LEN + 1

CMD_INIT = 0xFD          # byte[1] sentinel used by the vendor app at startup
DISPLAY_MAX = 199        # the vendor app clamps the displayed value here
LEVEL_MAX = 9            # the vendor app clamps level to one decimal digit

SRC_CPU = 0
SRC_GPU = 1

UNIT_C = 0
UNIT_F = 1

MIN_INTERVAL = 0.05      # a low-speed control pipe cannot usefully go faster

EX_OK = 0
EX_FAIL = 1
EX_CONFIG = 78           # sysexits.h EX_CONFIG: permanent, do not restart-loop

CONFIG_SECTION = "montech-hyperflow"
SYSTEM_CONFIG = "/etc/montech-hyperflow.conf"

# errnos that mean "this fd is no longer a usable path to the device"
_GONE = frozenset((errno.ENODEV, errno.ENXIO, errno.ESHUTDOWN, errno.EBADF,
                   errno.EIO, errno.EPIPE, errno.ENOENT, errno.EPROTO))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# HID report-descriptor parsing
#
# The previous version of this driver looked for the byte substrings
# b"\x06\x01\xff" and b"\x09\x01" in the descriptor.  A HID report descriptor
# is a self-delimiting item stream, so a substring search is unsound: the
# *data* bytes of one item can spell the header of another.  A four-byte
# Logical Maximum of 0xFF0106FF, for instance, contains "06 01 FF" while
# declaring no vendor page at all.  Walk the items properly instead.
# --------------------------------------------------------------------------

_MAIN_INPUT, _MAIN_OUTPUT, _MAIN_FEATURE = 0x8, 0x9, 0xB
_MAIN_COLLECTION, _MAIN_END_COLLECTION = 0xA, 0xC
_MAIN_KIND = {_MAIN_INPUT: "input", _MAIN_OUTPUT: "output",
              _MAIN_FEATURE: "feature"}


def parse_report_descriptor(rd):
    """Walk a HID report descriptor.

    Returns {(usage_page, usage, report_id, kind): data_bytes} for every
    Input/Output/Feature declared, keyed by the enclosing top-level
    application collection.  Malformed or truncated input is tolerated: the
    walk stops, it never raises.
    """
    g = {"usage_page": 0, "report_size": 0, "report_count": 0, "report_id": 0}
    gstack = []
    usages = []
    collections = []
    bits = {}
    i = 0
    while i < len(rd):
        b = rd[i]
        i += 1
        if b == 0xFE:                       # long item: [0xFE][size][tag][data]
            if i + 1 >= len(rd):
                break
            i += 2 + rd[i]
            continue
        size = b & 0x03
        if size == 3:
            size = 4
        item_type = (b >> 2) & 0x03
        tag = (b >> 4) & 0x0F
        if i + size > len(rd):
            break
        data = int.from_bytes(rd[i:i + size], "little") if size else 0
        i += size

        if item_type == 1:                                      # Global
            if tag == 0x0:
                g["usage_page"] = data
            elif tag == 0x7:
                g["report_size"] = data
            elif tag == 0x8:
                g["report_id"] = data
            elif tag == 0x9:
                g["report_count"] = data
            elif tag == 0xA:                                    # Push
                gstack.append(dict(g))
            elif tag == 0xB:                                    # Pop
                if gstack:
                    g = gstack.pop()
        elif item_type == 2:                                    # Local
            if tag == 0x0:                                      # Usage
                # a 4-byte Usage carries its page in the high half
                if size == 4:
                    usages.append((data >> 16, data & 0xFFFF))
                else:
                    usages.append((g["usage_page"], data))
        else:                                                   # Main
            if tag == _MAIN_COLLECTION:
                collections.append(usages[0] if usages
                                   else (g["usage_page"], 0))
            elif tag == _MAIN_END_COLLECTION:
                if collections:
                    collections.pop()
            elif tag in _MAIN_KIND:
                page, usage = (collections[0] if collections
                               else (g["usage_page"], 0))
                key = (page, usage, g["report_id"], _MAIN_KIND[tag])
                bits[key] = bits.get(key, 0) + \
                    g["report_size"] * g["report_count"]
            usages = []
    return {k: v // 8 for k, v in bits.items()}


def vendor_feature_len(rd):
    """Data-byte count of the vendor display feature report, or None."""
    reports = parse_report_descriptor(rd)
    return reports.get(
        (VENDOR_USAGE_PAGE, VENDOR_USAGE, REPORT_ID, "feature"))


# --------------------------------------------------------------------------
# hidraw ioctl plumbing
# --------------------------------------------------------------------------

def _ioc(direction, type_char, nr, size):
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | nr


def HIDIOCSFEATURE(size):
    # _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06, len)
    return _ioc(3, "H", 0x06, size)


def send_feature(fd, payload):
    """Issue SET_REPORT(Feature) with exactly len(payload) bytes on the wire.

    The size is derived from the buffer so the ioctl size field and the
    buffer can never drift apart.
    """
    buf = bytearray(payload)
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)


# --------------------------------------------------------------------------
# device discovery
# --------------------------------------------------------------------------

class Candidate:
    def __init__(self, path, sysdir, feature_len, stable):
        self.path = path
        self.sysdir = sysdir
        self.feature_len = feature_len      # data bytes, or None if unknown
        self.stable = stable                # /dev/input/by-id/... or None

    @property
    def frame_len(self):
        return (self.feature_len or FEATURE_DATA_LEN) + 1

    def __repr__(self):
        return "<%s frame_len=%d>" % (self.path, self.frame_len)


def _read_descriptor(sysdir):
    try:
        with open(os.path.join(sysdir, "report_descriptor"), "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def _stable_link(path):
    """A by-id symlink for this node, which survives replug; hidrawN does not."""
    for link in sorted(glob.glob("/dev/input/by-id/*-hidraw")):
        try:
            if os.path.realpath(link) == os.path.realpath(path):
                return link
        except OSError:
            continue
    return None


def _numeric_sort_key(node):
    """Numeric ordering, so hidraw2/hwmon2 sort before hidraw10/hwmon10."""
    m = re.search(r"(\d+)$", node)
    return (int(m.group(1)) if m else 1 << 30, node)


def find_devices(strict=True):
    """Return Candidates for the cooler's display interface."""
    found = []
    nodes = sorted(glob.glob("/sys/class/hidraw/hidraw*"), key=_numeric_sort_key)
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
        flen = vendor_feature_len(_read_descriptor(sysdir))
        if strict and flen is None:
            continue
        found.append(Candidate(path, sysdir, flen, _stable_link(path)))
    return found


class DeviceGone(Exception):
    """The device disappeared or the fd stopped being usable."""


class PumpDisplay:
    """An open handle to the pump-head display that can heal itself.

    A USB re-enumeration (replug, resume, hub reset) gives the device a new
    hidrawN.  Rediscovering on every reopen is what makes Restart=always
    unnecessary.
    """

    def __init__(self, explicit=None, strict=True, frame_len=None,
                 send_init=True):
        self.explicit = explicit
        self.strict = strict
        self.forced_frame_len = frame_len
        self.send_init = send_init
        self.fd = None
        self.path = None
        self.frame_len = frame_len or FRAME_LEN

    def _resolve(self):
        if self.explicit:
            sysdir = None
            real = os.path.realpath(self.explicit)
            base = os.path.basename(real)
            cand_sys = "/sys/class/hidraw/%s/device" % base
            if os.path.isdir(cand_sys):
                sysdir = cand_sys
            flen = vendor_feature_len(_read_descriptor(sysdir)) if sysdir else None
            return Candidate(self.explicit, sysdir, flen, None)
        devices = find_devices(strict=self.strict)
        if not devices:
            raise DeviceGone("no Montech HyperFlow Digital found "
                             "(expected USB %04x:%04x)" % (VID, PID))
        if len(devices) > 1:
            log("note: %d matching nodes (%s); using %s"
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
        if cand.feature_len is not None and self.forced_frame_len is None \
                and cand.feature_len != FEATURE_DATA_LEN:
            log("note: %s declares %d feature data bytes (expected %d); "
                "using a %d-byte frame"
                % (cand.path, cand.feature_len, FEATURE_DATA_LEN,
                   self.frame_len))
        if self.send_init:
            try:
                self.send(build_init_frame(self.frame_len))
                time.sleep(0.05)
            except DeviceGone:
                raise
            except OSError as exc:
                log("warning: init command failed (%s); continuing" % exc)
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


# --------------------------------------------------------------------------
# temperature sources
# --------------------------------------------------------------------------

def _hwmon_name(hwmon):
    try:
        with open(os.path.join(hwmon, "name")) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _label(inp):
    label_path = inp[:-len("_input")] + "_label"
    try:
        with open(label_path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


# Ordered best-first.  Package/die-wide readings are preferred over per-core
# ones; a super-I/O chip's PECI proxy is never preferred over the CPU's own
# sensor, because it is a filtered, board-specific copy that lags under load.
_CPU_DRIVERS = ("k10temp", "coretemp", "zenpower", "k8temp")
_PACKAGE_LABELS = ("Tctl", "Tdie", "Package id 0", "CPU Temperature")


def autodetect_cpu_sensor():
    """Find a package-level CPU temperature input, or None."""
    candidates = []
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*"),
                        key=_numeric_sort_key):
        name = _hwmon_name(hwmon)
        if name not in _CPU_DRIVERS:
            continue
        for inp in sorted(glob.glob(os.path.join(hwmon, "temp*_input"))):
            label = _label(inp)
            # lower is better; the third element keeps the sort total and
            # deterministic without letting the ASCII path decide, which
            # would rank temp10_input above temp1_input
            score = (_CPU_DRIVERS.index(name),
                     0 if label in _PACKAGE_LABELS else 1,
                     _sensor_index(inp))
            candidates.append((score, inp))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def _sensor_index(inp):
    m = re.search(r"temp(\d+)_input$", inp)
    return int(m.group(1)) if m else 1 << 30


def autodetect_gpu_sensor():
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*"),
                        key=_numeric_sort_key):
        if _hwmon_name(hwmon) in ("amdgpu", "radeon", "nouveau"):
            inp = os.path.join(hwmon, "temp1_input")
            if os.path.exists(inp):
                return inp
    return None


def nvidia_gpus():
    """[(index, name), ...] as nvidia-smi sees them, or [] if unavailable."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,name",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        return []
    gpus = []
    for line in out.decode(errors="replace").splitlines():
        parts = line.split(",", 1)
        if len(parts) == 2 and parts[0].strip().isdigit():
            gpus.append((int(parts[0].strip()), parts[1].strip()))
    return gpus


class Sensor:
    """Base: read() returns millidegrees Celsius, or None on failure."""

    description = "?"

    def read(self):
        raise NotImplementedError


class HwmonSensor(Sensor):
    """A hwmon temp*_input.

    hwmonN indices are probe-order dependent, so a cached path can go stale
    across a module reload.  On failure the path is re-resolved once through
    the same autodetection that produced it.
    """

    def __init__(self, path, redetect=None):
        self.path = path
        self.redetect = redetect
        self.description = path

    def read(self):
        value = self._read_path(self.path)
        if value is not None:
            return value
        if self.redetect is None:
            return None
        fresh = self.redetect()
        if fresh and fresh != self.path:
            log("sensor moved: %s -> %s" % (self.path, fresh))
            self.path = fresh
            self.description = fresh
            return self._read_path(self.path)
        return None

    @staticmethod
    def _read_path(path):
        try:
            with open(path) as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return None


class NvidiaSensor(Sensor):
    """nvidia-smi, for cards with no hwmon temperature input."""

    def __init__(self, index=0):
        self.index = index
        self.description = "nvidia-smi gpu %d" % index

    def read(self):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--id=%d" % self.index,
                 "--query-gpu=temperature.gpu",
                 "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=4)
        except Exception:
            return None
        text = out.decode(errors="replace").strip().splitlines()
        if not text:
            return None
        head = text[0].strip()
        if not head.lstrip("-").isdigit():      # "[N/A]" and friends
            return None
        return int(head) * 1000


class FixedSensor(Sensor):
    """--test-value: a constant, so the head shows a number we chose."""

    def __init__(self, celsius):
        self.celsius = celsius
        self.description = "fixed %d C (test mode)" % celsius

    def read(self):
        return self.celsius * 1000


# --------------------------------------------------------------------------
# frame encoding
# --------------------------------------------------------------------------

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
    digits are Fahrenheit.  The vendor computes it before the unit conversion
    (PROTOCOL.md, 0x00408dfe-0x00408e3b vs 0x00408e4a).  That asymmetry is
    deliberate; do not "fix" it.

    `level` may be overridden for experiments; it does not otherwise vary
    independently of the temperature.
    """
    celsius = max(0, int(celsius))
    if level is None:
        level = min(celsius // 10, LEVEL_MAX)
    level = max(0, min(int(level), 0x0F))

    if unit == UNIT_F:
        shown = int(celsius * 1.8 + 32)
    else:
        shown = celsius
    shown = min(shown, DISPLAY_MAX)          # vendor app clamps at 199

    buf = bytearray(frame_len)
    buf[0] = REPORT_ID
    buf[1] = (shown // 100) % 10
    buf[2] = (shown // 10) % 10
    buf[3] = shown % 10
    buf[4] = (level << 4) | (unit & 0x0F)
    buf[5] = source & 0xFF
    return bytes(buf)


def build_blank_frame(frame_len=FRAME_LEN):
    """All-zero payload: vendor app sends this to blank the display."""
    buf = bytearray(frame_len)
    buf[0] = REPORT_ID
    return bytes(buf)


def build_init_frame(frame_len=FRAME_LEN):
    """Startup command the vendor app emits before streaming temperatures."""
    buf = bytearray(frame_len)
    buf[0] = REPORT_ID
    buf[1] = CMD_INIT
    return bytes(buf)


def format_frame(frame, width=8):
    return " ".join("%02X" % b for b in frame[:width]) + " ..."


# --------------------------------------------------------------------------
# configuration file
# --------------------------------------------------------------------------

def config_search_path(explicit=None):
    if explicit:
        return [explicit]
    paths = []
    xdg = os.environ.get("XDG_CONFIG_HOME") or \
        os.path.join(os.path.expanduser("~"), ".config")
    paths.append(os.path.join(xdg, "montech-hyperflow.conf"))
    paths.append(SYSTEM_CONFIG)
    return paths


_CONFIG_KEYS = {
    "device": str, "source": str, "sensor": str, "gpu_index": int,
    "fahrenheit": bool, "interval": float, "blank_on_exit": bool,
    "no_init": bool, "rounding": str, "on_sensor_error": str,
    "sensor_error_blank_after": float, "frame_len": int,
}


def load_config(explicit=None):
    """Return (values, path_used). Missing files are not an error."""
    parser = configparser.ConfigParser()
    for path in config_search_path(explicit):
        if not os.path.exists(path):
            continue
        try:
            parser.read(path)
        except configparser.Error as exc:
            raise ValueError("%s: %s" % (path, exc))
        if not parser.has_section(CONFIG_SECTION):
            raise ValueError("%s: missing [%s] section"
                             % (path, CONFIG_SECTION))
        values = {}
        for key, value in parser.items(CONFIG_SECTION):
            key = key.replace("-", "_")
            if key not in _CONFIG_KEYS:
                raise ValueError("%s: unknown setting %r" % (path, key))
            kind = _CONFIG_KEYS[key]
            try:
                if kind is bool:
                    values[key] = parser.getboolean(CONFIG_SECTION, key)
                else:
                    values[key] = kind(value)
            except ValueError:
                raise ValueError("%s: %s=%r is not a valid %s"
                                 % (path, key, value, kind.__name__))
        return values, path
    return {}, None


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

_running = True


def _stop(signum, frame):
    global _running
    _running = False


def _to_celsius(millidegrees, rounding):
    if rounding == "truncate":
        return millidegrees // 1000
    return int(round(millidegrees / 1000.0))


def build_parser():
    ap = argparse.ArgumentParser(
        prog="montech-hyperflow",
        description="Drive the Montech HyperFlow Digital pump-head display.")
    ap.add_argument("--version", action="version",
                    version="montech-hyperflow " + __version__)
    ap.add_argument("--config", metavar="PATH",
                    help="config file (default: ~/.config/montech-hyperflow.conf, "
                         "then %s)" % SYSTEM_CONFIG)
    ap.add_argument("--device", help="explicit /dev/hidrawN (default: autodetect)")
    ap.add_argument("--source", choices=("cpu", "gpu"), default="cpu",
                    help="which temperature to display (default: cpu)")
    ap.add_argument("--sensor", help="explicit hwmon temp*_input path")
    ap.add_argument("--gpu-index", type=int, default=0,
                    help="nvidia-smi GPU index for --source gpu (default: 0)")
    ap.add_argument("--fahrenheit", action="store_true", help="display degF")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="update period in seconds (default: 1.0)")
    ap.add_argument("--rounding", choices=("nearest", "truncate"),
                    default="nearest",
                    help="millidegree -> degree conversion (default: nearest; "
                         "the vendor app truncates)")
    ap.add_argument("--on-sensor-error", choices=("hold", "blank"),
                    default="hold",
                    help="what to show when the sensor read fails "
                         "(default: hold the last good value; never 0)")
    ap.add_argument("--sensor-error-blank-after", type=float, default=30.0,
                    help="seconds of held value before blanking; 0 = hold "
                         "forever (default: 30)")
    ap.add_argument("--blank-on-exit", action="store_true",
                    help="clear the display when stopping")
    ap.add_argument("--no-init", action="store_true",
                    help="skip the 0xFD startup command")

    g = ap.add_argument_group("one-shot modes")
    g.add_argument("--list", action="store_true",
                   help="list matching devices and sensors, then exit")
    g.add_argument("--blank", action="store_true",
                   help="send a single blank frame and exit "
                        "(does not send the 0xFD init command)")
    g.add_argument("--once", action="store_true",
                   help="send exactly one temperature frame and exit")

    g = ap.add_argument_group("testing and experiments")
    g.add_argument("--test-value", type=int, metavar="CELSIUS",
                   help="display a fixed value instead of a live sensor. "
                        "Always in CELSIUS: it is the encoder's input, so "
                        "with --fahrenheit the head shows the converted "
                        "number. Bypasses sensor selection entirely.")
    g.add_argument("--test-level", type=int, metavar="N", choices=range(0, 10),
                   help="override the level nibble (0-9) independently of the "
                        "temperature, for the Phase 2 level sweep")
    g.add_argument("--frame-len", type=int, metavar="N",
                   help="force the on-wire frame length in bytes "
                        "(default: from the report descriptor, normally 64)")
    g.add_argument("--dry-run", action="store_true",
                   help="print frames instead of writing to the device")
    g.add_argument("--any-interface", action="store_true",
                   help="do not require the 0xFF01 vendor collection "
                        "(unsafe: may select the keyboard interface)")
    return ap


def do_list(args):
    strict_devs = find_devices(strict=True)
    all_devs = find_devices(strict=False)
    print("matching hidraw nodes (0xFF01 vendor collection):")
    if strict_devs:
        for d in strict_devs:
            extra = "  stable: %s" % d.stable if d.stable else ""
            print("  %s  feature report %d data bytes -> %d-byte frame%s"
                  % (d.path, d.feature_len, d.frame_len, extra))
    else:
        print("  (none)")
    print("all %04x:%04x nodes:" % (VID, PID))
    for d in all_devs:
        kind = ("vendor display" if d.feature_len is not None
                else "no vendor collection - do not write to this one")
        try:
            st = os.stat(d.path)
            perms = "%04o %s" % (st.st_mode & 0o7777,
                                 "writable" if os.access(d.path, os.W_OK)
                                 else "NOT writable by you")
        except OSError:
            perms = "cannot stat"
        print("  %-16s %-48s %s" % (d.path, kind, perms))
    if not all_devs:
        print("  (none) - is the internal USB header connected?")

    cpu = autodetect_cpu_sensor()
    print("cpu sensor: %s" % (cpu or "(none)"))
    if cpu:
        value = HwmonSensor._read_path(cpu)
        if value is not None:
            print("            reads %.1f C  [%s]"
                  % (value / 1000.0, _label(cpu) or "unlabelled"))
    gpu = autodetect_gpu_sensor()
    print("gpu sensor: %s" % (gpu or "(no hwmon gpu sensor)"))
    for index, name in nvidia_gpus():
        temp = NvidiaSensor(index).read()
        print("            nvidia-smi %d: %s%s"
              % (index, name,
                 "  reads %d C" % (temp // 1000) if temp is not None else ""))
    return EX_OK


def pick_sensor(args):
    """Returns (Sensor, error_message)."""
    if args.test_value is not None:
        return FixedSensor(args.test_value), None
    if args.sensor:
        if not os.path.exists(args.sensor):
            return None, "sensor path does not exist: %s" % args.sensor
        if HwmonSensor._read_path(args.sensor) is None:
            return None, "sensor path is not readable as an integer: %s" \
                % args.sensor
        return HwmonSensor(args.sensor), None
    if args.source == "gpu":
        path = autodetect_gpu_sensor()
        if path:
            return HwmonSensor(path, redetect=autodetect_gpu_sensor), None
        gpus = nvidia_gpus()
        if not gpus:
            return None, ("no GPU temperature source found "
                          "(no amdgpu/radeon/nouveau hwmon, no nvidia-smi)")
        indices = [i for i, _ in gpus]
        if args.gpu_index not in indices:
            return None, ("--gpu-index %d not present; nvidia-smi reports %s"
                          % (args.gpu_index,
                             ", ".join("%d=%s" % g for g in gpus)))
        if len(gpus) > 1:
            log("note: %d GPUs present (%s); using index %d"
                % (len(gpus), ", ".join("%d=%s" % g for g in gpus),
                   args.gpu_index))
        return NvidiaSensor(args.gpu_index), None
    path = autodetect_cpu_sensor()
    if not path:
        return None, "no CPU temperature source found; pass --sensor"
    return HwmonSensor(path, redetect=autodetect_cpu_sensor), None


def main(argv=None):
    ap = build_parser()
    pre, _ = ap.parse_known_args(argv)
    try:
        config, config_path = load_config(pre.config)
    except ValueError as exc:
        log("config error: %s" % exc)
        return EX_CONFIG
    if pre.config and not os.path.exists(pre.config):
        log("config error: %s does not exist" % pre.config)
        return EX_CONFIG
    if config:
        ap.set_defaults(**config)
    args = ap.parse_args(argv)
    if config_path:
        log("using config %s" % config_path)

    # --- install signal handlers before anything touches the device --------
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    if args.list:
        return do_list(args)

    # --- validate ----------------------------------------------------------
    if args.interval <= 0:
        log("config error: --interval must be > 0")
        return EX_CONFIG
    if args.interval < MIN_INTERVAL:
        log("note: raising --interval %.3f to the %.2fs floor"
            % (args.interval, MIN_INTERVAL))
        args.interval = MIN_INTERVAL
    if args.frame_len is not None and not (2 <= args.frame_len <= 4096):
        log("config error: --frame-len out of range")
        return EX_CONFIG
    if args.any_interface and not args.device:
        log("warning: --any-interface may select the keyboard interface of "
            "this device, which has no display. Pass --device to be explicit.")

    unit = UNIT_F if args.fahrenheit else UNIT_C
    source = SRC_GPU if args.source == "gpu" else SRC_CPU

    # --- blank is a true one-shot: no init frame, no sensor, no loop -------
    if args.blank:
        if args.dry_run:
            print("blank -> " + format_frame(
                build_blank_frame(args.frame_len or FRAME_LEN)))
            return EX_OK
        display = PumpDisplay(explicit=args.device,
                              strict=not args.any_interface,
                              frame_len=args.frame_len, send_init=False)
        try:
            display.open()
            display.send(build_blank_frame(display.frame_len))
        except PermissionError as exc:
            log("error: %s" % exc)
            return EX_CONFIG
        except (DeviceGone, OSError) as exc:
            log("error: %s" % exc)
            return EX_FAIL
        finally:
            display.close()
        return EX_OK

    # --- pick a sensor -----------------------------------------------------
    sensor, why = pick_sensor(args)
    if sensor is None:
        log("error: %s" % why)
        return EX_CONFIG
    log("source: %s" % sensor.description)

    # --- open the device ---------------------------------------------------
    display = None
    if not args.dry_run:
        display = PumpDisplay(explicit=args.device,
                              strict=not args.any_interface,
                              frame_len=args.frame_len,
                              send_init=not args.no_init)
        try:
            display.open()
        except PermissionError as exc:
            log("error: %s" % exc)
            return EX_CONFIG
        except DeviceGone as exc:
            log("error: %s. Is the internal USB header connected? "
                "Try --list." % exc)
            return EX_FAIL
        log("device: %s (%d-byte frames)" % (display.path, display.frame_len))
    else:
        frame_len = args.frame_len or FRAME_LEN
        if not args.no_init:
            print("init  -> " + format_frame(build_init_frame(frame_len)))

    frame_len = display.frame_len if display else (args.frame_len or FRAME_LEN)

    status = _Loop(args, sensor, display, unit, source, frame_len)
    rc = status.run()

    if display is not None:
        if args.blank_on_exit and display.is_open:
            try:
                display.send(build_blank_frame(display.frame_len))
            except (DeviceGone, OSError):
                pass
        display.close()
    elif args.dry_run and args.blank_on_exit:
        print("blank -> " + format_frame(build_blank_frame(frame_len)))
    return rc


class _Loop:
    """The 1 Hz update loop, with reconnect and sensor-failure policy."""

    def __init__(self, args, sensor, display, unit, source, frame_len):
        self.args = args
        self.sensor = sensor
        self.display = display
        self.unit = unit
        self.source = source
        self.frame_len = frame_len
        self.last_good = None            # celsius
        self.first_failure = None        # monotonic time
        self.reconnect_delay = 1.0
        self.warned_gone = False

    def _value(self):
        """(celsius or None, blanked_reason or None)."""
        raw = self.sensor.read()
        now = time.monotonic()
        if raw is not None:
            self.last_good = _to_celsius(raw, self.args.rounding)
            if self.first_failure is not None:
                log("sensor recovered")
                self.first_failure = None
            return self.last_good, None
        if self.first_failure is None:
            self.first_failure = now
            log("sensor read failed (%s); %s"
                % (self.sensor.description,
                   "holding %d C" % self.last_good if self.last_good is not None
                   else "no previous value, blanking"))
        if self.last_good is None or self.args.on_sensor_error == "blank":
            return None, "sensor unavailable"
        held = now - self.first_failure
        limit = self.args.sensor_error_blank_after
        if limit and held >= limit:
            return None, "sensor unavailable for %.0fs" % held
        return self.last_good, None

    def _emit(self, celsius, blank_reason):
        if blank_reason is not None:
            frame = build_blank_frame(self.frame_len)
            label = "blank (%s)" % blank_reason
        else:
            frame = build_frame(celsius, unit=self.unit, source=self.source,
                                frame_len=self.frame_len,
                                level=self.args.test_level)
            label = "%3d C" % celsius
        if self.display is None:
            print("%-28s -> %s" % (label, format_frame(frame)), flush=True)
            return True
        try:
            self.display.send(frame)
        except DeviceGone as exc:
            if not self.warned_gone:
                log("%s; will reopen" % exc)
                self.warned_gone = True
            return False
        except OSError as exc:
            log("write failed: %s" % exc)
            return False
        self.warned_gone = False
        self.reconnect_delay = 1.0
        return True

    def _reconnect(self):
        try:
            self.display.open()
        except PermissionError as exc:
            log("error: %s" % exc)
            return False
        except (DeviceGone, OSError):
            return False
        log("reopened %s (%d-byte frames)"
            % (self.display.path, self.display.frame_len))
        self.frame_len = self.display.frame_len
        self.reconnect_delay = 1.0
        return True

    def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while _running and time.monotonic() < end:
            time.sleep(min(0.1, max(0.0, end - time.monotonic())))

    def run(self):
        while _running:
            if self.display is not None and not self.display.is_open:
                if not self._reconnect():
                    self._sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 2, 30.0)
                    continue

            celsius, blank_reason = self._value()
            self._emit(celsius, blank_reason)

            if self.args.once:
                break
            self._sleep(self.args.interval)
        return EX_OK


if __name__ == "__main__":
    sys.exit(main())

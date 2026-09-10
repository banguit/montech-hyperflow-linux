"""Temperature sources. Everything returns millidegrees Celsius, or None."""

import glob
import os
import re
import subprocess

from .device import numeric_sort_key

# Ordered best-first. Package/die-wide readings beat per-core ones. A
# super-I/O chip's PECI proxy is deliberately NOT in this list: it is a
# filtered, board-specific copy of the CPU reading that lags under load, so it
# must never be preferred over the CPU's own sensor.
CPU_DRIVERS = ("k10temp", "coretemp", "zenpower", "k8temp")
PACKAGE_LABELS = ("Tctl", "Tdie", "Package id 0", "CPU Temperature")
GPU_DRIVERS = ("amdgpu", "radeon", "nouveau")


def hwmon_name(hwmon):
    try:
        with open(os.path.join(hwmon, "name")) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def label_of(inp):
    try:
        with open(inp[:-len("_input")] + "_label") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _sensor_index(inp):
    m = re.search(r"temp(\d+)_input$", inp)
    return int(m.group(1)) if m else 1 << 30


def autodetect_cpu_sensor():
    """Find a package-level CPU temperature input, or None."""
    candidates = []
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*"),
                        key=numeric_sort_key):
        name = hwmon_name(hwmon)
        if name not in CPU_DRIVERS:
            continue
        for inp in sorted(glob.glob(os.path.join(hwmon, "temp*_input"))):
            # lower is better; the third element keeps the sort total and
            # deterministic without letting the ASCII path decide, which
            # would rank temp10_input above temp1_input
            score = (CPU_DRIVERS.index(name),
                     0 if label_of(inp) in PACKAGE_LABELS else 1,
                     _sensor_index(inp))
            candidates.append((score, inp))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def autodetect_gpu_sensor():
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*"),
                        key=numeric_sort_key):
        if hwmon_name(hwmon) in GPU_DRIVERS:
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


def read_hwmon(path):
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


class HwmonSensor(Sensor):
    """A hwmon temp*_input.

    hwmonN indices are probe-order dependent, so a cached path can go stale
    across a module reload. On failure the path is re-resolved once through
    the same autodetection that produced it.
    """

    def __init__(self, path, redetect=None, log=None):
        self.path = path
        self.redetect = redetect
        self.log = log or (lambda _msg: None)
        self.description = path

    def read(self):
        value = read_hwmon(self.path)
        if value is not None:
            return value
        if self.redetect is None:
            return None
        fresh = self.redetect()
        if fresh and fresh != self.path:
            self.log("sensor moved: %s -> %s" % (self.path, fresh))
            self.path = fresh
            self.description = fresh
            return read_hwmon(self.path)
        return None


class NvidiaSensor(Sensor):
    """nvidia-smi, for cards that expose no hwmon temperature."""

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
        lines = out.decode(errors="replace").strip().splitlines()
        if not lines:
            return None
        head = lines[0].strip()
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


def to_celsius(millidegrees, rounding="nearest"):
    if rounding == "truncate":
        return millidegrees // 1000
    return int(round(millidegrees / 1000.0))

#!/usr/bin/env python3
"""
montech-hyperflow - drive the digital display on a Montech HyperFlow Digital
240/360 AIO pump head from Linux.

Protocol reverse-engineered from "HyperFlow Digital setup 1.0.1.5.exe"
(DeviceDriver.exe, TCOMAS OEM stack). See PROTOCOL.md.

USB 1a2c:4e85, vendor HID collection UsagePage 0xFF01 / Usage 0x01.
65-byte HID *feature* report, report ID 0x07, pushed once per second.

No third-party dependencies: talks to /dev/hidrawN via HIDIOCSFEATURE.
"""

import argparse
import fcntl
import glob
import os
import re
import signal
import struct
import sys
import time

VID = 0x1A2C
PID = 0x4E85

REPORT_ID = 0x07
REPORT_LEN = 65          # 1 report-ID byte + 64 payload
CMD_INIT = 0xFD          # byte[1] sentinel used by the vendor app at startup

SRC_CPU = 0
SRC_GPU = 1

UNIT_C = 0
UNIT_F = 1


# --------------------------------------------------------------------------
# hidraw ioctl plumbing
# --------------------------------------------------------------------------

def _ioc(direction, type_char, nr, size):
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | nr


def HIDIOCSFEATURE(size):
    # _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06, len)
    return _ioc(3, "H", 0x06, size)


def send_feature(fd, payload):
    buf = bytearray(payload)
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)


# --------------------------------------------------------------------------
# device discovery
# --------------------------------------------------------------------------

def _has_vendor_collection(sysdir):
    """True if the report descriptor declares Usage Page 0xFF01, Usage 0x01.

    The pump head exposes more than one HID interface; the vendor app picks
    the one with this vendor-defined collection, so we do the same.
    """
    path = os.path.join(sysdir, "report_descriptor")
    try:
        with open(path, "rb") as fh:
            rd = fh.read()
    except OSError:
        return False
    # 06 01 FF = Usage Page (vendor 0xFF01);  09 01 = Usage (0x01)
    return b"\x06\x01\xff" in rd and b"\x09\x01" in rd


def find_devices(strict=True):
    """Return candidate /dev/hidrawN paths for the cooler."""
    found = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = os.path.join(node, "device", "uevent")
        try:
            with open(uevent) as fh:
                text = fh.read()
        except OSError:
            continue
        m = re.search(r"HID_ID=[0-9A-Fa-f]+:0*([0-9A-Fa-f]+):0*([0-9A-Fa-f]+)", text)
        if not m:
            continue
        if int(m.group(1), 16) != VID or int(m.group(2), 16) != PID:
            continue
        if strict and not _has_vendor_collection(os.path.join(node, "device")):
            continue
        found.append("/dev/" + os.path.basename(node))
    return found


# --------------------------------------------------------------------------
# temperature sources
# --------------------------------------------------------------------------

def _read_int(path):
    with open(path) as fh:
        return int(fh.read().strip())


def _hwmon_name(hwmon):
    try:
        with open(os.path.join(hwmon, "name")) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def autodetect_cpu_sensor():
    """Find a sensible package-level CPU temperature input.

    Preference order: k10temp Tctl (AMD), coretemp Package id 0 (Intel),
    zenpower, then any temp1_input on a known CPU driver.
    """
    preferred = ("k10temp", "coretemp", "zenpower", "k8temp")
    candidates = []
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = _hwmon_name(hwmon)
        if name not in preferred:
            continue
        for inp in sorted(glob.glob(os.path.join(hwmon, "temp*_input"))):
            label_path = inp.replace("_input", "_label")
            label = ""
            if os.path.exists(label_path):
                try:
                    with open(label_path) as fh:
                        label = fh.read().strip()
                except OSError:
                    pass
            score = preferred.index(name) * 100
            if label in ("Tctl", "Package id 0", "Tdie"):
                score -= 50
            candidates.append((score, inp, name, label))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def autodetect_gpu_sensor():
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if _hwmon_name(hwmon) in ("amdgpu", "radeon", "nouveau"):
            inp = os.path.join(hwmon, "temp1_input")
            if os.path.exists(inp):
                return inp
    return None


def read_nvidia_temp():
    """Fallback for NVIDIA cards, which expose no hwmon temperature."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=5)
        return int(out.decode().strip().splitlines()[0])
    except Exception:
        return None


# --------------------------------------------------------------------------
# frame encoding
# --------------------------------------------------------------------------

def build_frame(celsius, unit=UNIT_C, source=SRC_CPU):
    """Encode one display frame.

    byte 0 : 0x07  report ID
    byte 1 : hundreds digit of the displayed value
    byte 2 : tens digit
    byte 3 : ones digit
    byte 4 : (level << 4) | unit    level = min(celsius // 10, 9)
    byte 5 : 0 = CPU, 1 = GPU
    6..64  : zero

    NOTE: `level` is always derived from the CELSIUS reading even when the
    digits are Fahrenheit. That is what the vendor firmware expects; it drives
    the colour/intensity ramp on the head.
    """
    celsius = max(0, int(celsius))
    level = min(celsius // 10, 9)

    if unit == UNIT_F:
        shown = int(celsius * 1.8 + 32) if celsius else 0
    else:
        shown = celsius
    shown = min(shown, 199)          # vendor app clamps at 199

    buf = bytearray(REPORT_LEN)
    buf[0] = REPORT_ID
    buf[1] = (shown // 100) % 10
    buf[2] = (shown // 10) % 10
    buf[3] = shown % 10
    buf[4] = (level << 4) | (unit & 0x0F)
    buf[5] = source & 0xFF
    return bytes(buf)


def build_blank_frame():
    """All-zero payload: vendor app sends this to blank the display."""
    buf = bytearray(REPORT_LEN)
    buf[0] = REPORT_ID
    return bytes(buf)


def build_init_frame():
    """Startup command the vendor app emits before streaming temperatures."""
    buf = bytearray(REPORT_LEN)
    buf[0] = REPORT_ID
    buf[1] = CMD_INIT
    return bytes(buf)


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------

_running = True


def _stop(signum, frame):
    global _running
    _running = False


def main():
    ap = argparse.ArgumentParser(
        description="Drive the Montech HyperFlow Digital pump-head display.")
    ap.add_argument("--device", help="explicit /dev/hidrawN (default: autodetect)")
    ap.add_argument("--source", choices=("cpu", "gpu"), default="cpu",
                    help="which temperature to display (default: cpu)")
    ap.add_argument("--sensor", help="explicit hwmon temp*_input path")
    ap.add_argument("--fahrenheit", action="store_true", help="display degF")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="update period in seconds (default: 1.0)")
    ap.add_argument("--blank-on-exit", action="store_true",
                    help="clear the display when stopping")
    ap.add_argument("--no-init", action="store_true",
                    help="skip the 0xFD startup command")
    ap.add_argument("--list", action="store_true",
                    help="list matching devices and sensors, then exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="print frames instead of writing to the device")
    ap.add_argument("--any-interface", action="store_true",
                    help="do not require the 0xFF01 vendor collection")
    args = ap.parse_args()

    unit = UNIT_F if args.fahrenheit else UNIT_C
    source = SRC_GPU if args.source == "gpu" else SRC_CPU

    if args.list:
        devs = find_devices(strict=not args.any_interface)
        print("matching hidraw nodes:", ", ".join(devs) if devs else "(none)")
        print("all 1a2c:4e85 nodes  :",
              ", ".join(find_devices(strict=False)) or "(none)")
        print("cpu sensor           :", autodetect_cpu_sensor() or "(none)")
        print("gpu sensor           :", autodetect_gpu_sensor() or "(none; try nvidia-smi)")
        return 0

    # --- pick a sensor -----------------------------------------------------
    sensor = args.sensor
    use_nvidia = False
    if not sensor:
        if source == SRC_GPU:
            sensor = autodetect_gpu_sensor()
            if not sensor:
                if read_nvidia_temp() is None:
                    print("error: no GPU temperature source found", file=sys.stderr)
                    return 1
                use_nvidia = True
        else:
            sensor = autodetect_cpu_sensor()
            if not sensor:
                print("error: no CPU temperature source found; pass --sensor",
                      file=sys.stderr)
                return 1

    # --- open the device ---------------------------------------------------
    fd = None
    if not args.dry_run:
        path = args.device
        if not path:
            devs = find_devices(strict=not args.any_interface)
            if not devs:
                print("error: no Montech HyperFlow Digital found "
                      "(expected USB 1a2c:4e85). Is the internal USB header "
                      "connected? Try --list.", file=sys.stderr)
                return 1
            path = devs[0]
        try:
            fd = os.open(path, os.O_RDWR)
        except PermissionError:
            print(f"error: permission denied on {path}. Install the udev rule "
                  "or run as root.", file=sys.stderr)
            return 1
        if not args.no_init:
            try:
                send_feature(fd, build_init_frame())
                time.sleep(0.05)
            except OSError as exc:
                print(f"warning: init command failed ({exc}); continuing",
                      file=sys.stderr)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while _running:
        if use_nvidia:
            temp = read_nvidia_temp()
            celsius = temp if temp is not None else 0
        else:
            try:
                celsius = _read_int(sensor) // 1000
            except (OSError, ValueError):
                celsius = 0

        frame = build_frame(celsius, unit=unit, source=source)

        if args.dry_run:
            print(f"{celsius:3d}C -> " + " ".join(f"{b:02X}" for b in frame[:8]) + " ...")
        else:
            try:
                send_feature(fd, frame)
            except OSError as exc:
                print(f"write failed: {exc}", file=sys.stderr)
                return 1

        # sleep in slices so SIGTERM is responsive
        end = time.monotonic() + args.interval
        while _running and time.monotonic() < end:
            time.sleep(min(0.1, max(0.0, end - time.monotonic())))

    if fd is not None:
        if args.blank_on_exit:
            try:
                send_feature(fd, build_blank_frame())
            except OSError:
                pass
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())

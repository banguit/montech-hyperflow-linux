"""Command-line entry point for the daemon."""

import argparse
import os
import signal
import sys

from . import __version__
from . import config as configmod
from .daemon import MIN_INTERVAL, Loop
from .device import (PumpDisplay, DeviceGone, VID, PID, find_devices)
from .frame import (FRAME_LEN, SRC_CPU, SRC_GPU, UNIT_C, UNIT_F,
                    build_blank_frame, build_init_frame, format_frame)
from .sensors import (FixedSensor, HwmonSensor, NvidiaSensor,
                      autodetect_cpu_sensor, autodetect_gpu_sensor,
                      label_of, nvidia_gpus, read_hwmon)
from .status import StatusWriter

EX_OK = 0
EX_FAIL = 1
EX_CONFIG = 78           # sysexits.h EX_CONFIG: permanent, do not restart-loop

_running = True


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def _stop(_signum, _frame):
    global _running
    _running = False


def build_parser():
    ap = argparse.ArgumentParser(
        prog="montech-hyperflow",
        description="Drive the Montech HyperFlow Digital pump-head display.")
    ap.add_argument("--version", action="version",
                    version="montech-hyperflow " + __version__)
    ap.add_argument("--config", metavar="PATH",
                    help="config file (default: %s, then %s)"
                         % (configmod.user_config_path(),
                            configmod.SYSTEM_CONFIG))
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
    g.add_argument("--status", action="store_true",
                   help="print the running daemon's published status and exit")

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


def do_list():
    strict = find_devices(strict=True)
    everything = find_devices(strict=False)
    print("matching hidraw nodes (0xFF01 vendor collection):")
    if strict:
        for d in strict:
            extra = "  stable: %s" % d.stable if d.stable else ""
            print("  %s  feature report %d data bytes -> %d-byte frame%s"
                  % (d.path, d.feature_len, d.frame_len, extra))
    else:
        print("  (none)")
    print("all %04x:%04x nodes:" % (VID, PID))
    for d in everything:
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
    if not everything:
        print("  (none) - is the internal USB header connected?")

    cpu = autodetect_cpu_sensor()
    print("cpu sensor: %s" % (cpu or "(none)"))
    if cpu:
        value = read_hwmon(cpu)
        if value is not None:
            print("            reads %.1f C  [%s]"
                  % (value / 1000.0, label_of(cpu) or "unlabelled"))
    print("gpu sensor: %s" % (autodetect_gpu_sensor() or "(no hwmon gpu sensor)"))
    for index, name in nvidia_gpus():
        temp = NvidiaSensor(index).read()
        print("            nvidia-smi %d: %s%s"
              % (index, name,
                 "  reads %d C" % (temp // 1000) if temp is not None else ""))
    return EX_OK


def do_status():
    from . import status as statusmod
    record = statusmod.read()
    if record is None:
        print("no running daemon has published status "
              "(looked in %s)" % statusmod.STATUS_PATH)
        return EX_FAIL
    order = ("celsius", "displayed", "unit", "source", "level", "sensor",
             "device", "frame_len", "interval", "blanked", "connected",
             "age", "stale")
    for key in order:
        if key in record:
            value = record[key]
            if key == "age":
                value = "%.1fs" % value
            print("%-12s %s" % (key, value))
    return EX_OK


def pick_sensor(args):
    """Returns (Sensor, error_message)."""
    if args.test_value is not None:
        return FixedSensor(args.test_value), None
    if args.sensor:
        if not os.path.exists(args.sensor):
            return None, "sensor path does not exist: %s" % args.sensor
        if read_hwmon(args.sensor) is None:
            return None, ("sensor path is not readable as an integer: %s"
                          % args.sensor)
        return HwmonSensor(args.sensor, log=log), None
    if args.source == "gpu":
        path = autodetect_gpu_sensor()
        if path:
            return HwmonSensor(path, redetect=autodetect_gpu_sensor,
                               log=log), None
        gpus = nvidia_gpus()
        if not gpus:
            return None, ("no GPU temperature source found "
                          "(no amdgpu/radeon/nouveau hwmon, no nvidia-smi)")
        if args.gpu_index not in [i for i, _ in gpus]:
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
    return HwmonSensor(path, redetect=autodetect_cpu_sensor, log=log), None


def main(argv=None):
    ap = build_parser()
    pre, _ = ap.parse_known_args(argv)
    if pre.config and not os.path.exists(pre.config):
        log("config error: %s does not exist" % pre.config)
        return EX_CONFIG
    try:
        values, config_path = configmod.load(pre.config)
    except ValueError as exc:
        log("config error: %s" % exc)
        return EX_CONFIG
    if values:
        ap.set_defaults(**values)
    args = ap.parse_args(argv)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    if args.list:
        return do_list()
    if args.status:
        return do_status()
    if config_path:
        log("using config %s" % config_path)

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

    if args.blank:
        return _do_blank(args)

    sensor, why = pick_sensor(args)
    if sensor is None:
        log("error: %s" % why)
        return EX_CONFIG
    log("source: %s" % sensor.description)

    display = None
    if args.dry_run:
        frame_len = args.frame_len or FRAME_LEN
        if not args.no_init:
            print("init  -> " + format_frame(build_init_frame(frame_len)))
    else:
        display = PumpDisplay(explicit=args.device,
                              strict=not args.any_interface,
                              frame_len=args.frame_len,
                              send_init=not args.no_init, log=log)
        try:
            display.open()
        except PermissionError as exc:
            log("error: %s" % exc)
            return EX_CONFIG
        except DeviceGone as exc:
            log("error: %s. Is the internal USB header connected? Try --list."
                % exc)
            return EX_FAIL
        frame_len = display.frame_len
        log("device: %s (%d-byte frames)" % (display.path, frame_len))

    status = StatusWriter()
    loop = Loop(sensor, display, unit=unit, source=source,
                frame_len=frame_len, interval=args.interval,
                rounding=args.rounding,
                on_sensor_error=args.on_sensor_error,
                sensor_error_blank_after=args.sensor_error_blank_after,
                level=args.test_level, once=args.once, log=log,
                status=status, is_running=lambda: _running)
    rc = loop.run()

    if display is not None:
        if args.blank_on_exit and display.is_open:
            try:
                display.send(build_blank_frame(display.frame_len))
            except (DeviceGone, OSError):
                pass
        display.close()
    elif args.dry_run and args.blank_on_exit:
        print("blank -> " + format_frame(build_blank_frame(frame_len)))
    status.clear()
    return rc


def _do_blank(args):
    if args.dry_run:
        print("blank -> " + format_frame(
            build_blank_frame(args.frame_len or FRAME_LEN)))
        return EX_OK
    display = PumpDisplay(explicit=args.device,
                          strict=not args.any_interface,
                          frame_len=args.frame_len, send_init=False, log=log)
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

"""The update loop: read a sensor, encode a frame, push it, publish status."""

import time

from .device import DeviceGone
from .frame import (UNIT_C, UNIT_F, build_blank_frame, build_frame,
                    displayed_value, format_frame)
from .sensors import to_celsius
from .status import StatusWriter

MIN_INTERVAL = 0.05      # a low-speed control pipe cannot usefully go faster
MAX_RECONNECT_DELAY = 30.0


class Loop:
    """1 Hz update loop, with reconnect and a sensor-failure policy.

    The policy matters: a failed sensor read must never be encoded as a real
    0 C frame, because 0 C is a plausible temperature and the head has no way
    to say "I don't know".
    """

    def __init__(self, sensor, display, *, unit=UNIT_C, source=0,
                 frame_len=64, interval=1.0, rounding="nearest",
                 on_sensor_error="hold", sensor_error_blank_after=30.0,
                 level=None, once=False, log=None, status=None,
                 is_running=None):
        self.sensor = sensor
        self.display = display
        self.unit = unit
        self.source = source
        self.frame_len = frame_len
        self.interval = max(interval, MIN_INTERVAL)
        self.rounding = rounding
        self.on_sensor_error = on_sensor_error
        self.sensor_error_blank_after = sensor_error_blank_after
        self.level = level
        self.once = once
        self.log = log or (lambda _msg: None)
        self.status = status if status is not None else StatusWriter()
        self.is_running = is_running or (lambda: True)

        self.last_good = None            # celsius
        self.first_failure = None        # monotonic
        self.reconnect_delay = 1.0
        self.warned_gone = False

    # -- sensor policy ----------------------------------------------------

    def value(self):
        """(celsius or None, blank_reason or None)."""
        raw = self.sensor.read()
        now = time.monotonic()
        if raw is not None:
            self.last_good = to_celsius(raw, self.rounding)
            if self.first_failure is not None:
                self.log("sensor recovered")
                self.first_failure = None
            return self.last_good, None

        if self.first_failure is None:
            self.first_failure = now
            self.log("sensor read failed (%s); %s"
                     % (self.sensor.description,
                        "holding %d C" % self.last_good
                        if self.last_good is not None
                        else "no previous value, blanking"))
        if self.last_good is None or self.on_sensor_error == "blank":
            return None, "sensor unavailable"
        held = now - self.first_failure
        limit = self.sensor_error_blank_after
        if limit and held >= limit:
            return None, "sensor unavailable for %.0fs" % held
        return self.last_good, None

    # -- one tick ---------------------------------------------------------

    def emit(self, celsius, blank_reason):
        if blank_reason is not None:
            frame = build_blank_frame(self.frame_len)
            label = "blank (%s)" % blank_reason
        else:
            frame = build_frame(celsius, unit=self.unit, source=self.source,
                                frame_len=self.frame_len, level=self.level)
            label = "%3d C" % celsius

        ok = True
        if self.display is None:                       # --dry-run
            print("%-28s -> %s" % (label, format_frame(frame)), flush=True)
        else:
            try:
                self.display.send(frame)
                self.warned_gone = False
                self.reconnect_delay = 1.0
            except DeviceGone as exc:
                if not self.warned_gone:
                    self.log("%s; will reopen" % exc)
                    self.warned_gone = True
                ok = False
            except OSError as exc:
                self.log("write failed: %s" % exc)
                ok = False

        self.publish(celsius, blank_reason, ok)
        return ok

    def publish(self, celsius, blank_reason, ok):
        self.status.write(
            celsius=celsius,
            displayed=(None if blank_reason is not None
                       else displayed_value(celsius, self.unit)),
            unit="F" if self.unit == UNIT_F else "C",
            source="gpu" if self.source else "cpu",
            sensor=self.sensor.description,
            device=(self.display.path if self.display is not None else None),
            frame_len=self.frame_len,
            interval=self.interval,
            blanked=blank_reason,
            connected=bool(ok and (self.display is None
                                   or self.display.is_open)),
            level=(None if blank_reason is not None
                   else (self.level if self.level is not None
                         else min((celsius or 0) // 10, 9))),
        )

    # -- reconnect --------------------------------------------------------

    def reconnect(self):
        try:
            self.display.open()
        except PermissionError as exc:
            self.log("error: %s" % exc)
            return False
        except (DeviceGone, OSError):
            return False
        self.log("reopened %s (%d-byte frames)"
                 % (self.display.path, self.display.frame_len))
        self.frame_len = self.display.frame_len
        self.reconnect_delay = 1.0
        return True

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while self.is_running() and time.monotonic() < end:
            time.sleep(min(0.1, max(0.0, end - time.monotonic())))

    # -- run --------------------------------------------------------------

    def run(self):
        while self.is_running():
            if self.display is not None and not self.display.is_open:
                if not self.reconnect():
                    self.publish(self.last_good, "device disconnected", False)
                    self.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 2,
                                               MAX_RECONNECT_DELAY)
                    continue
            celsius, blank_reason = self.value()
            self.emit(celsius, blank_reason)
            if self.once:
                break
            self.sleep(self.interval)
        return 0

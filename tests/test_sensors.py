"""Sensor selection, rounding, and the never-display-zero failure policy."""

import time
import unittest

from montech_hyperflow import sensors as S
from montech_hyperflow.daemon import Loop
from montech_hyperflow.device import numeric_sort_key
from montech_hyperflow.frame import SRC_CPU, UNIT_C


class SortKeys(unittest.TestCase):

    def test_numeric_not_lexicographic(self):
        nodes = ["/sys/class/hwmon/hwmon10", "/sys/class/hwmon/hwmon2",
                 "/sys/class/hwmon/hwmon1"]
        self.assertEqual([n.rsplit("hwmon", 1)[1]
                          for n in sorted(nodes, key=numeric_sort_key)],
                         ["1", "2", "10"])


class Rounding(unittest.TestCase):

    def test_nearest_vs_truncate(self):
        self.assertEqual(S.to_celsius(41499, "nearest"), 41)
        self.assertEqual(S.to_celsius(41500, "nearest"), 42)
        self.assertEqual(S.to_celsius(41999, "truncate"), 41)
        self.assertEqual(S.to_celsius(41000, "truncate"), 41)


class SensorPreferences(unittest.TestCase):

    def test_peci_proxies_are_not_cpu_drivers(self):
        # it87/nct6xxx expose an "Intel PECI" reading that lags under load.
        # It must never outrank the CPU's own sensor.
        for name in ("it87", "it8620", "it8792", "nct6775", "nct6687"):
            self.assertNotIn(name, S.CPU_DRIVERS)

    def test_package_labels_outrank_per_core(self):
        self.assertIn("Package id 0", S.PACKAGE_LABELS)
        self.assertIn("Tctl", S.PACKAGE_LABELS)
        self.assertNotIn("Core 0", S.PACKAGE_LABELS)


class _Flaky(S.Sensor):
    description = "flaky"

    def __init__(self, values):
        self.values = list(values)

    def read(self):
        return self.values.pop(0) if self.values else None


class _NullStatus:
    def write(self, **_kw):
        pass

    def clear(self):
        pass


class SensorFailurePolicy(unittest.TestCase):
    """A failed read must never be encoded as a real 0 C frame."""

    def _loop(self, sensor, **overrides):
        kwargs = dict(unit=UNIT_C, source=SRC_CPU, frame_len=64,
                      once=True, status=_NullStatus())
        kwargs.update(overrides)
        return Loop(sensor, None, **kwargs)

    def test_holds_last_good_value(self):
        loop = self._loop(_Flaky([45000, None, None]))
        self.assertEqual(loop.value(), (45, None))
        self.assertEqual(loop.value(), (45, None))
        self.assertEqual(loop.value(), (45, None))

    def test_blanks_when_there_was_never_a_reading(self):
        celsius, reason = self._loop(_Flaky([None])).value()
        self.assertIsNone(celsius)
        self.assertIsNotNone(reason)

    def test_never_reports_zero_for_a_failure(self):
        for values in ([None], [None, None], [45000, None]):
            loop = self._loop(_Flaky(list(values)))
            for _ in values:
                celsius, reason = loop.value()
                self.assertFalse(celsius == 0 and reason is None,
                                 "a failed read was encoded as a real 0 C")

    def test_blank_mode_blanks_immediately(self):
        loop = self._loop(_Flaky([45000, None]), on_sensor_error="blank")
        self.assertEqual(loop.value(), (45, None))
        self.assertIsNone(loop.value()[0])

    def test_hold_expires_into_a_blank(self):
        loop = self._loop(_Flaky([45000, None, None]),
                          sensor_error_blank_after=0.001)
        loop.value()
        loop.value()
        time.sleep(0.01)
        self.assertIsNone(loop.value()[0])

    def test_hold_forever_when_limit_is_zero(self):
        loop = self._loop(_Flaky([45000] + [None] * 5),
                          sensor_error_blank_after=0)
        loop.value()
        for _ in range(5):
            self.assertEqual(loop.value(), (45, None))

    def test_recovery_resumes_live_values(self):
        loop = self._loop(_Flaky([45000, None, 60000]))
        loop.value()
        loop.value()
        self.assertEqual(loop.value(), (60, None))

    def test_zero_celsius_is_a_real_reading_not_a_failure(self):
        celsius, reason = self._loop(_Flaky([0])).value()
        self.assertEqual(celsius, 0)
        self.assertIsNone(reason)


class NvidiaProbeReporting(unittest.TestCase):
    """The GPU probe must say WHY it failed.

    Regression: "no nvidia-smi" was reported on a machine where nvidia-smi is
    installed and working for the desktop user, but the systemd sandbox was
    blocking /dev/nvidia*. That message sends people to install a package
    they already have.
    """

    def test_missing_binary_is_distinguished(self):
        import shutil as _sh
        real = _sh.which
        S_mod = __import__("montech_hyperflow.sensors", fromlist=["x"])
        S_mod.shutil.which = lambda n: None
        try:
            gpus, err = S_mod.nvidia_probe()
        finally:
            S_mod.shutil.which = real
        self.assertEqual(gpus, [])
        self.assertIn("not installed", err)

    def test_driver_failure_mentions_the_sandbox(self):
        S_mod = __import__("montech_hyperflow.sensors", fromlist=["x"])
        real_which, real_run = S_mod.shutil.which, S_mod.subprocess.run

        class _Proc:
            returncode = 255
            stdout = b""
            stderr = b"Failed to initialize NVML: Unknown Error\n"

        S_mod.shutil.which = lambda n: "/usr/bin/nvidia-smi"
        S_mod.subprocess.run = lambda *a, **k: _Proc()
        try:
            gpus, err = S_mod.nvidia_probe()
        finally:
            S_mod.shutil.which, S_mod.subprocess.run = real_which, real_run
        self.assertEqual(gpus, [])
        self.assertIn("NVML", err)
        self.assertIn("DeviceAllow", err)      # points at the actual fix

    def test_success_returns_no_error(self):
        S_mod = __import__("montech_hyperflow.sensors", fromlist=["x"])
        real_which, real_run = S_mod.shutil.which, S_mod.subprocess.run

        class _Proc:
            returncode = 0
            stdout = b"0, Card A\n1, Card B\n"
            stderr = b""

        S_mod.shutil.which = lambda n: "/usr/bin/nvidia-smi"
        S_mod.subprocess.run = lambda *a, **k: _Proc()
        try:
            gpus, err = S_mod.nvidia_probe()
        finally:
            S_mod.shutil.which, S_mod.subprocess.run = real_which, real_run
        self.assertEqual(gpus, [(0, "Card A"), (1, "Card B")])
        self.assertIsNone(err)


class GpuMenuSelection(unittest.TestCase):

    def setUp(self):
        from montech_hyperflow.tray import service
        self.service = service

    def test_live_gpu_index_wins(self):
        record = {"source": "gpu", "gpu_index": 1, "stale": False}
        self.assertEqual(self.service.selected_gpu_index(record, {}), 1)

    def test_config_index_used_when_stale(self):
        stale = {"source": "gpu", "gpu_index": 0, "stale": True}
        self.assertEqual(
            self.service.selected_gpu_index(stale, {"gpu_index": 1}), 1)

    def test_defaults_to_zero(self):
        self.assertEqual(self.service.selected_gpu_index(None, {}), 0)

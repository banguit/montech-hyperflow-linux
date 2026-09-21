"""Talking to the system daemon: read its status, drive it via systemd.

No IPC protocol and no D-Bus binding. Status is the atomic JSON file the
daemon publishes to /run; control is systemctl (which raises a polkit prompt
through the session's authentication agent) and a pkexec'd admin helper for
the config file.
"""

import os
import shutil
import subprocess

from .. import config as configmod
from .. import sensors as sensorsmod
from .. import status as statusmod

UNIT = "montech-hyperflow.service"
ADMIN = "montech-hyperflow-admin"


def read_status():
    return statusmod.read()


def read_settings():
    """What is *configured*, as opposed to what is *live*.

    The status file is the truth about what the head is showing, but it only
    exists while the daemon is running and publishing. The menu's ticks are a
    statement about intent, so they come from the config file and stay correct
    across a restart, a stopped service, or a daemon that cannot publish.
    Returns {} if nothing is configured, which means built-in defaults.
    """
    for path in (configmod.SYSTEM_CONFIG, configmod.user_config_path()):
        try:
            values, used = configmod.load(path)
        except ValueError:
            continue
        if used:
            return values
    return {}


def _run(argv, timeout=60):
    """(ok, combined_output). Never raises."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        return False, "%s not found" % argv[0]
    except subprocess.TimeoutExpired:
        return False, "%s timed out" % argv[0]
    except Exception as exc:                       # pragma: no cover
        return False, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out.strip()


def unit_state():
    """'active' | 'inactive' | 'failed' | 'not-installed' | 'unknown'."""
    if not shutil.which("systemctl"):
        return "unknown"
    ok, out = _run(["systemctl", "is-active", UNIT], timeout=10)
    state = out.splitlines()[0].strip() if out else ""
    if state:
        if state == "inactive":
            ok2, out2 = _run(["systemctl", "is-enabled", UNIT], timeout=10)
            if not ok2 and "No such file" in out2:
                return "not-installed"
        return state
    return "unknown"


def unit_enabled():
    """'enabled' | 'disabled' | 'static' | 'masked' | 'unknown'.

    Note this is independent of unit_state(): a service can be running now
    and still not start at boot, which is exactly what happens when it is
    started from this menu rather than enabled.
    """
    ok, out = _run(["systemctl", "is-enabled", UNIT], timeout=10)
    return out.splitlines()[0].strip() if out else "unknown"


def starts_at_boot():
    return unit_enabled() == "enabled"


# --- tray autostart -------------------------------------------------------
#
# Separate from the service, and deliberately so. The service starts at BOOT
# and drives the display whether or not anyone logs in; the tray starts at
# LOGIN and only shows what the service is doing. Conflating them would mean
# either no display until someone logs in, or an icon nobody asked for.
AUTOSTART_BASENAME = "montech-hyperflow-tray.desktop"


def autostart_path():
    base = os.environ.get("XDG_CONFIG_HOME") or \
        os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "autostart", AUTOSTART_BASENAME)


def starts_at_login():
    path = autostart_path()
    if not os.path.exists(path):
        return False
    # A file with Hidden=true or the GNOME flag set to false is how desktops
    # record "installed but switched off"; treat either as off.
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return False
    for line in text.splitlines():
        key = line.strip().lower()
        if key in ("hidden=true", "x-gnome-autostart-enabled=false"):
            return False
    return True


def set_autostart(enabled):
    """(ok, message). Writes under the user's own config; no polkit needed."""
    path = autostart_path()
    if not enabled:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            return False, "could not remove %s: %s" % (path, exc)
        return True, ""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(AUTOSTART_DESKTOP)
        os.replace(tmp, path)
    except OSError as exc:
        return False, "could not write %s: %s" % (path, exc)
    return True, ""


AUTOSTART_DESKTOP = """[Desktop Entry]
Type=Application
Name=Montech HyperFlow Digital tray
Comment=Show the AIO pump-head temperature in the panel
Exec=montech-hyperflow-tray
Icon=montech-hyperflow
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""


def start():
    return _run(["systemctl", "start", UNIT])


def stop():
    return _run(["systemctl", "stop", UNIT])


def restart():
    return _run(["systemctl", "restart", UNIT])


def set_enabled(enabled):
    return _run(["systemctl", "enable" if enabled else "disable", UNIT])


def apply_settings(**settings):
    """Persist settings to the system config, then restart the daemon.

    Runs the admin helper under pkexec; polkit decides whether to prompt.
    """
    if not settings:
        return True, ""
    args = ["%s=%s" % (k, ("yes" if v is True else "no" if v is False else v))
            for k, v in settings.items()]
    helper = shutil.which(ADMIN) or "/usr/bin/" + ADMIN
    ok, out = _run(["pkexec", helper, "set"] + args)
    if not ok:
        return False, out or "the privileged helper was cancelled or failed"
    if unit_state() == "active":
        return restart()
    return True, out


def selected_unit(record, settings):
    """Which unit the menu should tick: live if publishing, else configured.

    The status file is the truth about what the head is *showing*; the config
    file is the truth about what was *chosen*. A tick is a statement about
    intent, so it must survive a stopped service, a restart, or a daemon that
    cannot publish -- otherwise the menu silently reverts to the built-in
    default and contradicts both the config and the display.
    """
    if record and not record.get("stale") and record.get("unit"):
        return record["unit"]
    return "F" if settings.get("fahrenheit") else "C"


def selected_source(record, settings):
    if record and not record.get("stale") and record.get("source"):
        return record["source"]
    return settings.get("source", "cpu")


def gpu_choices():
    """([(index_or_None, label), ...], error_or_None).

    Empty with an error means the menu should show a disabled entry saying
    why, rather than an option that puts the service into 78/CONFIG when
    clicked. That is not hypothetical: a sandboxed unit that blocks
    /dev/nvidia* has a working nvidia-smi that cannot reach the driver.
    """
    return sensorsmod.gpu_sources()


def selected_gpu_index(record, settings):
    if record and not record.get("stale") and record.get("source") == "gpu":
        idx = record.get("gpu_index")
        if idx is not None:
            return idx
    return settings.get("gpu_index", 0)

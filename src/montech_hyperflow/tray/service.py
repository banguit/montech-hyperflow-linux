"""Talking to the system daemon: read its status, drive it via systemd.

No IPC protocol and no D-Bus binding. Status is the atomic JSON file the
daemon publishes to /run; control is systemctl (which raises a polkit prompt
through the session's authentication agent) and a pkexec'd admin helper for
the config file.
"""

import shutil
import subprocess

from .. import config as configmod
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
    ok, out = _run(["systemctl", "is-enabled", UNIT], timeout=10)
    return out.splitlines()[0].strip() if out else "unknown"


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

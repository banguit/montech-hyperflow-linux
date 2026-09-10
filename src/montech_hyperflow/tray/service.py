"""Talking to the system daemon: read its status, drive it via systemd.

No IPC protocol and no D-Bus binding. Status is the atomic JSON file the
daemon publishes to /run; control is systemctl (which raises a polkit prompt
through the session's authentication agent) and a pkexec'd admin helper for
the config file.
"""

import shutil
import subprocess

from .. import status as statusmod

UNIT = "montech-hyperflow.service"
ADMIN = "montech-hyperflow-admin"


def read_status():
    return statusmod.read()


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

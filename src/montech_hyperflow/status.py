"""The daemon's published status, and how the tray reads it.

Deliberately a file, not an IPC protocol. The daemon stays standard-library
only, the tray needs no client library, and anything can `cat` it. Writes are
atomic (write-and-rename), so a reader never sees a half-written record.
"""

import json
import os
import time

RUNTIME_DIR = "/run/montech-hyperflow"
STATUS_PATH = os.path.join(RUNTIME_DIR, "status.json")

# How old a status file has to be before the tray treats it as stale rather
# than current. Generous: the daemon's default interval is 1 s, but a user may
# configure a slower one.
STALE_AFTER = 15.0


def _fallback_dir():
    run = os.environ.get("XDG_RUNTIME_DIR")
    if run:
        return os.path.join(run, "montech-hyperflow")
    return None


class StatusWriter:
    """Publishes daemon state. Never raises: status is not load-bearing."""

    def __init__(self, path=STATUS_PATH):
        self.path = path
        self.enabled = True
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            return
        except OSError:
            pass
        alt = _fallback_dir()
        if alt:
            try:
                os.makedirs(alt, exist_ok=True)
                self.path = os.path.join(alt, "status.json")
                return
            except OSError:
                pass
        self.enabled = False

    def write(self, **fields):
        if not self.enabled:
            return
        record = dict(fields)
        record["updated"] = time.time()
        tmp = "%s.%d.tmp" % (self.path, os.getpid())
        try:
            with open(tmp, "w") as fh:
                json.dump(record, fh)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            self.enabled = False

    def clear(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass


def search_paths(path=STATUS_PATH):
    """Every location `read` will look in, in order."""
    paths = [path]
    alt = _fallback_dir()
    if alt:
        paths.append(os.path.join(alt, "status.json"))
    return paths


def read(path=STATUS_PATH):
    """Return the daemon's last published status, or None.

    A record older than STALE_AFTER is returned with stale=True rather than
    hidden, so the UI can say "last seen 4 minutes ago" instead of "unknown".
    """
    for candidate in search_paths(path):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate) as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        age = time.time() - float(record.get("updated", 0))
        record["age"] = age
        record["stale"] = age > STALE_AFTER
        return record
    return None

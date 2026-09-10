"""Privileged helper, run via pkexec by the tray.

Writes /etc/montech-hyperflow.conf and nothing else. It takes settings as
KEY=VALUE arguments and validates every one against config.KEYS before
writing, so it cannot be talked into emitting arbitrary file content, and it
never accepts a target path from the caller.
"""

import sys

from . import config as configmod

USAGE = "usage: montech-hyperflow-admin set KEY=VALUE [KEY=VALUE ...]"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "set":
        print(USAGE, file=sys.stderr)
        return 2

    existing, _ = configmod.load(configmod.SYSTEM_CONFIG)
    values = dict(existing)
    for item in argv[1:]:
        if "=" not in item:
            print("bad argument %r; %s" % (item, USAGE), file=sys.stderr)
            return 2
        key, _, value = item.partition("=")
        key = key.strip().replace("-", "_")
        if key not in configmod.KEYS:
            print("unknown setting %r" % key, file=sys.stderr)
            return 2
        kind = configmod.KEYS[key]
        text = value.strip()
        try:
            if kind is bool:
                values[key] = text.lower() in ("1", "yes", "true", "on")
            else:
                values[key] = kind(text)
        except ValueError:
            print("%s=%r is not a valid %s" % (key, text, kind.__name__),
                  file=sys.stderr)
            return 2

    try:
        path = configmod.save(configmod.SYSTEM_CONFIG, values)
    except OSError as exc:
        print("cannot write %s: %s" % (configmod.SYSTEM_CONFIG, exc),
              file=sys.stderr)
        return 1
    print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

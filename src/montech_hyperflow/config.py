"""Configuration file handling.

INI via configparser, so it is stdlib-only and hand-editable. Command-line
flags always win: the loaded values become argparse defaults, which argparse
then overrides with anything actually passed.
"""

import configparser
import os

SECTION = "montech-hyperflow"
SYSTEM_CONFIG = "/etc/montech-hyperflow.conf"

KEYS = {
    "device": str, "source": str, "sensor": str, "gpu_index": int,
    "fahrenheit": bool, "interval": float, "blank_on_exit": bool,
    "no_init": bool, "rounding": str, "on_sensor_error": str,
    "sensor_error_blank_after": float, "frame_len": int,
}


def user_config_path():
    xdg = os.environ.get("XDG_CONFIG_HOME") or \
        os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(xdg, "montech-hyperflow.conf")


def search_path(explicit=None):
    if explicit:
        return [explicit]
    return [user_config_path(), SYSTEM_CONFIG]


def load(explicit=None):
    """Return (values, path_used). A missing file is not an error."""
    parser = configparser.ConfigParser()
    for path in search_path(explicit):
        if not os.path.exists(path):
            continue
        try:
            parser.read(path)
        except configparser.Error as exc:
            raise ValueError("%s: %s" % (path, exc))
        if not parser.has_section(SECTION):
            raise ValueError("%s: missing [%s] section" % (path, SECTION))
        values = {}
        for key, value in parser.items(SECTION):
            key = key.replace("-", "_")
            if key not in KEYS:
                raise ValueError("%s: unknown setting %r" % (path, key))
            kind = KEYS[key]
            try:
                if kind is bool:
                    values[key] = parser.getboolean(SECTION, key)
                else:
                    values[key] = kind(value)
            except ValueError:
                raise ValueError("%s: %s=%r is not a valid %s"
                                 % (path, key, value, kind.__name__))
        return values, path
    return {}, None


def save(path, values):
    """Write a config file atomically, preserving nothing else.

    Used by the tray's privileged helper. Values are validated against KEYS
    first so the helper cannot be talked into writing arbitrary content.
    """
    clean = {}
    for key, value in values.items():
        key = key.replace("-", "_")
        if key not in KEYS:
            raise ValueError("unknown setting %r" % key)
        kind = KEYS[key]
        if kind is bool:
            clean[key] = "yes" if value else "no"
        else:
            clean[key] = str(kind(value))
    parser = configparser.ConfigParser()
    parser[SECTION] = clean
    tmp = path + ".tmp"
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(tmp, "w") as fh:
        fh.write("# Written by montech-hyperflow. Hand edits are fine.\n")
        parser.write(fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path

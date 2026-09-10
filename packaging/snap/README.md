# Snap packaging

> **Unofficial community driver.** Not produced, endorsed or supported by
> Montech. Hardware: USB **`1a2c:4e85`**, OEM platform **TCOMAS DH-C100**.
> No vendor code or artwork is included. See [`NOTICE.md`](../../NOTICE.md).

**Read the confinement section before you install this.** A strictly confined
snap cannot do most of what this driver needs a host to do. On a normal
desktop, install the distro package or run `sudo make install` from the source
tree. This snap exists for Ubuntu Core and appliance images, where a gadget
snap can hand it the hidraw slot it needs.

## Build

`snapcraft` only looks for `snap/snapcraft.yaml`, `snapcraft.yaml` or
`.snapcraft.yaml` under the project root, so point it at this directory:

```bash
cd /path/to/montech-hyperflow
ln -s packaging/snap snap      # gives snapcraft the snap/snapcraft.yaml it wants
snapcraft
```

The part builds from the local tree (`source: .`) through the Makefile's
`DESTDIR`/`PREFIX` contract — the same one the `.deb` and the RPM use — so the
snap contains exactly the files a distro package would, minus the
host-integration pieces described below.

Install the result unsigned:

```bash
sudo snap install --dangerous ./montech-hyperflow_1.0.0_amd64.snap
```

### Why `base: core22`

* The indicator is GTK 3 + `AyatanaAppIndicator3`. The `gnome` extension on
  core22 pulls in the **gnome-42-2204** platform snap, where GTK 3 is a
  first-class citizen. The core24 extension targets gnome-46-2404, which is
  GTK4-first; nothing in this app wants a newer toolkit.
* core22 is Ubuntu 22.04 → Python 3.10, comfortably above the 3.8 floor.
* Ubuntu 22.04 is supported well past the point where this driver stops being
  interesting.

### Architecture

The driver itself is pure Python and architecture-independent, but the snap is
not: it stages a Python interpreter and the compiled appindicator libraries.
`snapcraft.yaml` therefore declares `amd64` and `arm64` builds. This is the one
place where the "`Architecture: all` / `BuildArch: noarch`" rule the other
packages follow does not apply.

## Apps

| App | Command | What it is |
| --- | --- | --- |
| `montech-hyperflow` | `montech-hyperflow` | the driver; a `daemon: simple` service, and the CLI |
| `tray` | `montech-hyperflow.tray` | the panel indicator |

The service is installed **disabled** (`install-mode: disable`), matching the
other packages: installing a driver must not start driving hardware. Opt in
with:

```bash
sudo snap start --enable montech-hyperflow
snap logs -f montech-hyperflow
```

The daemon app doubles as the CLI, so `montech-hyperflow --list` works from a
shell (the `--blank-on-exit` in the app's `command:` is prepended to whatever
you pass; the one-shot modes ignore it).

## Device access: the `hidraw` plug

`hidraw` is a real snapd interface and it never auto-connects:

```bash
sudo snap connect montech-hyperflow:hidraw <slot-snap>:<slot-name>
```

**On a classic desktop this will almost certainly fail with "no matching
slot".** `hidraw` slots are declared by a *gadget* snap, for example:

```yaml
# in the gadget snap's snap.yaml, on Ubuntu Core
slots:
  montech-hyperflow:
    interface: hidraw
    usb-vendor: 0x1a2c
    usb-product: 0x4e85
```

There is no gadget snap on a classic system, so there is nothing to connect
to. Check what your system actually offers with `snap interface hidraw`. If it
lists no slots, this snap cannot reach the device at all, and no amount of
`snap connect` will change that. That is the single biggest reason the snap is
not the recommended way to install this driver.

`raw-usb` is **not** a workaround: it grants `/dev/bus/usb` (libusb-style)
access, and this driver deliberately speaks to `/dev/hidrawN` with
`HIDIOCSFEATURE` instead.

The daemon also plugs `hardware-observe`, which is what lets it read
`/sys/class/hwmon/**` for the CPU temperature. Without it there is no
temperature to display.

## What strict confinement breaks

Everything in this section is a consequence of confinement, not a bug in the
packaging. Nothing here can be fixed inside a strict snap.

| Host integration | Status in the snap |
| --- | --- |
| `72-montech-hyperflow.rules` udev rule | **not installed.** A snap cannot write `/etc/udev/rules.d`. snapd generates its own device-cgroup tagging for a *connected* interface instead — which is why the `hidraw` connection is the whole ball game here. Nothing gives you the `/dev/montech-hyperflow` symlink or the `plugdev` group access that the rule provides on the host. |
| `montech-hyperflow.service` | **not used.** snapd generates `snap.montech-hyperflow.montech-hyperflow.service` from the app definition. The hardening in the shipped unit (`DynamicUser`, `SupplementaryGroups=plugdev`, the syscall filter, `DeviceAllow=char-hidraw rw`) is replaced by snapd's confinement, which is a different set of trade-offs — notably the snap daemon runs as **root**, where the system unit runs as a transient unprivileged user. |
| system-sleep hook | **not installed.** `/usr/lib/systemd/system-sleep/` is off limits. The display is **not** blanked before suspend, and the daemon is not stopped and restarted around it. |
| polkit action | **not installed**, and `pkexec` is not reachable from inside confinement. The tray's *Apply settings* path (`pkexec montech-hyperflow-admin set …`) fails. |
| `systemctl` from the tray | **fails.** The tray shells out to `systemctl` to start/stop/enable the service; that binary is not in the snap and the host's system bus is not reachable. The service menu will read "not installed". Use `snap start` / `snap stop` instead. |
| `/run/montech-hyperflow/status.json` | **denied.** AppArmor does not allow a snap to write an arbitrary `/run` path, and snapd refuses layouts that target `/run`, so the usual layout trick cannot redirect it. The daemon app therefore has `XDG_RUNTIME_DIR=$SNAP_COMMON`, which pushes the status file onto the code's documented fallback: `/var/snap/montech-hyperflow/common/montech-hyperflow/status.json`. |
| tray ↔ daemon status | **broken between the two apps.** The tray must keep its real `XDG_RUNTIME_DIR` (Wayland and D-Bus sockets live there), so it looks in `/run/montech-hyperflow` and in `/run/user/$UID/snap.montech-hyperflow/…` — neither of which is where the daemon just wrote. The indicator will show "no daemon" even while the snap's own daemon is running. Read the file directly instead: `sudo cat /var/snap/montech-hyperflow/common/montech-hyperflow/status.json`. |
| `/etc/montech-hyperflow.conf` | **unreadable.** The daemon app sets `XDG_CONFIG_HOME=$SNAP_COMMON`, so its config file is `/var/snap/montech-hyperflow/common/montech-hyperflow.conf` (root-owned, hand-edited, same INI keys as the example in `packaging/`). The tray keeps its own copy under `$SNAP_USER_DATA/.config/` and the two do not see each other. |
| the panel icon | **probably generic.** With AppIndicator the *host* panel resolves the icon name, and `montech-hyperflow` is only in the snap's icon theme. The tray's fallback logic checks the icon theme it can see — the snap's — so it will confidently pass a name the panel cannot resolve. |
| `--source gpu` | **unavailable.** It shells out to `nvidia-smi`, which is not in the snap and cannot be executed from the host. |
| `RestartPreventExitStatus=78` | **no equivalent.** A permanent misconfiguration (exit 78, `EX_CONFIG`) restart-loops every 5 s here instead of stopping. Watch `snap logs montech-hyperflow`. |

## What actually works

Given a hidraw slot to connect to (i.e. Ubuntu Core with a gadget that
declares one):

* device discovery, the frame protocol and the display update loop — the
  driver code is unchanged and unpatched;
* `hardware-observe` for the hwmon CPU sensor;
* the config file, at the `$SNAP_COMMON` path above;
* the status file, at the `$SNAP_COMMON` path above;
* `snap start` / `snap stop` / `snap logs` as the service interface;
* the tray *renders* (GTK 3 and the indicator come from the gnome extension
  plus the staged Ayatana libraries) — but as a status client it is blind, per
  the table above.

## Store naming

"Montech" and "HyperFlow" are somebody else's names. This snap uses them
descriptively, for a driver that is explicitly labelled unofficial in its
title, summary and description. A snap store may still refuse or require
transfer of a name containing a trademark; if you are publishing rather than
building locally, expect to rename (`montech-hyperflow-unofficial`, or a name
built from the OEM platform ID `tcomas-dh-c100`) and keep the disclaimer in
the summary and description either way.

## Uninstall

```bash
sudo snap remove --purge montech-hyperflow
```

`--purge` also removes `/var/snap/montech-hyperflow/`, which is where this
snap's config and status live.

## Verification status

`snapcraft` is not installed in the environment where this file was written,
so **no snap has been built from this manifest**. What was actually checked:

* `snapcraft.yaml` parses as YAML (PyYAML), and the key names, app names and
  part names are the ones written above;
* the two `override-*` scripts pass `sh -n`;
* the version-extraction `sed` returns `1.0.0`, matching `make version`;
* `make install-core install-tray DESTDIR=… PREFIX=/usr` produces the layout
  the `command:` and `desktop:` paths point at
  (`usr/bin/montech-hyperflow`, `usr/bin/montech-hyperflow-tray`,
  `usr/lib/montech-hyperflow/montech_hyperflow/`,
  `usr/share/applications/montech-hyperflow-tray.desktop`).

Not checked, because it needs snapcraft, snapd or the hardware: the build
itself, whether the staged interpreter and `python3-gi` line up at runtime,
the AppArmor behaviour described above, and anything involving a real
`hidraw` slot.

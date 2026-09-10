# montech-hyperflow

Drive the 7-segment display on a **Montech HyperFlow Digital 240/360** AIO pump
head from Linux, with a panel indicator and a system service.

> **Unofficial community driver.** Not produced, endorsed or supported by
> Montech. Montech's own app is Windows-only; this is a clean-room-documented
> Linux port of the wire protocol, written from scratch. See
> [`NOTICE.md`](NOTICE.md).
>
> Hardware: USB **`1a2c:4e85`** (SEMICO controller), OEM platform
> **TCOMAS DH-C100**. Other rebrands of the same pump head very likely work
> unchanged — if yours does, please open an issue and say so.

The daemon is **pure Python standard library**: no hidapi, no libusb, no
pyudev, no kernel module. It talks to `/dev/hidrawN` directly. GUI
dependencies live only in the optional tray package.

![status](https://img.shields.io/badge/phase%201-confirmed%20on%20hardware-brightgreen)
![license](https://img.shields.io/badge/license-GPL--3.0-blue)

## Status

**Working and confirmed on real hardware:** device discovery, the 64-byte
frame, digit rendering, live CPU temperature tracking `sensors` to the degree
under load, non-root access, reopen after unplug.

**Still unknown:** what the `level` nibble and byte 5 actually *look* like on
the head. Those need a human looking at the pump head, so they are written up
as reproducible experiments in [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md)
rather than guessed at in code.

Every protocol claim carries its confidence level in
[`docs/PROTOCOL.md`](docs/PROTOCOL.md), with the `DeviceDriver.exe` address
behind it.

## Install

```bash
make test          # hardware-free; should be all green
sudo make install  # daemon, udev rule, service, sleep hook, docs
sudo make install-tray   # optional: the panel indicator
```

Then check it found the device — this writes nothing:

```bash
montech-hyperflow --list
```

```
matching hidraw nodes (0xFF01 vendor collection):
  /dev/hidraw5  feature report 63 data bytes -> 64-byte frame  stable: /dev/montech-hyperflow
all 1a2c:4e85 nodes:
  /dev/hidraw4     no vendor collection - do not write to this one  0600 NOT writable by you
  /dev/hidraw5     vendor display                                   0660 writable
cpu sensor: /sys/class/hwmon/hwmon3/temp1_input
            reads 41.0 C  [Package id 0]
```

`hidraw5` must say **writable**. If not, see [Permissions](#permissions).

Try it in the foreground before installing the service:

```bash
montech-hyperflow --test-value 42     # a fixed, known number
montech-hyperflow                     # live CPU temperature
```

Then:

```bash
sudo systemctl enable --now montech-hyperflow
```

### Packages

CI builds `.deb`, `.rpm`, an Arch `PKGBUILD`, an AppImage, a Flatpak and a
Snap. See [Which package should I use?](#which-package-should-i-use) — it is
not a free choice, because this software needs a udev rule and a system
service, which sandboxed formats cannot install.

## The panel indicator

`montech-hyperflow-tray` puts the temperature the head is showing into your
panel, with the controls worth having one click away: °C/°F, CPU/GPU,
start/stop the service, blank the display.

It is a **client**. It never opens the device — the system service owns that,
so the head keeps working when you log out or sit at the login screen. Two
writers to one display would fight at 1 Hz.

How it talks to the daemon, deliberately boringly:

| Direction | Mechanism |
|---|---|
| status | the daemon writes `/run/montech-hyperflow/status.json` atomically each tick; the tray reads it |
| start/stop | `systemctl`, which raises a polkit prompt through your session's auth agent |
| settings | a tiny `pkexec` helper that validates every key before writing `/etc/montech-hyperflow.conf` |

No D-Bus binding, no IPC protocol, no daemon dependencies. You can `cat` the
status file.

> **GNOME users:** tray icons need the *AppIndicator and KStatusNotifierItem*
> extension. On Ubuntu it is installed and enabled by default. On stock GNOME,
> install `gnome-shell-extension-appindicator` and enable it.

### Light and dark themes

The panel uses the **symbolic** icons, which the shell recolours to match the
panel foreground, so they are correct on light and dark themes automatically
and stay crisp at 16px. The full-colour icon is used only where it sits on a
known background at a readable size: the About dialog and the app grid.

### Using your own icon

The icon theme searches `$XDG_DATA_HOME` before the system directories, so
dropping a file in `~/.local/share/icons/` overrides the shipped one for your
user, with no configuration and no rebuild:

```bash
mkdir -p ~/.local/share/icons/hicolor/symbolic/apps
cp my-icon.svg ~/.local/share/icons/hicolor/symbolic/apps/montech-hyperflow-symbolic.svg
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
```

The names the tray looks for, in order:

| Purpose | Names tried |
|---|---|
| panel, running | `montech-hyperflow-symbolic`, `montech-hyperflow` |
| panel, stopped | `montech-hyperflow-idle-symbolic`, `montech-hyperflow-idle`, then the running names |
| About dialog | `montech-hyperflow` |

A file you place there is yours and stays on your machine. Note that this
project cannot ship a manufacturer's logo as its icon — see
[`NOTICE.md`](NOTICE.md) — but nothing stops you using one locally.

## Usage

```
montech-hyperflow [--source cpu|gpu] [--sensor PATH] [--gpu-index N]
                  [--fahrenheit] [--interval SEC] [--rounding nearest|truncate]
                  [--on-sensor-error hold|blank] [--sensor-error-blank-after SEC]
                  [--device PATH] [--blank-on-exit] [--no-init] [--config PATH]
                  [--list] [--status] [--blank] [--once]
                  [--test-value C] [--test-level N] [--frame-len N] [--dry-run]
```

```bash
montech-hyperflow --list                       # devices, permissions, sensors
montech-hyperflow --status                     # what the running daemon is doing
montech-hyperflow --dry-run                    # print frames, write nothing
montech-hyperflow --test-value 123             # hold a known number on the head
montech-hyperflow --blank                      # one-shot clear, then exit
montech-hyperflow --source gpu --gpu-index 1   # second GPU
```

`--test-value` is always in **Celsius** — it is the encoder's input, so with
`--fahrenheit` the head shows the converted number. It bypasses sensor
selection entirely, so you can test a display on a machine whose sensors are
broken.

### Configuration

`~/.config/montech-hyperflow.conf`, then `/etc/montech-hyperflow.conf`. See
[`packaging/montech-hyperflow.conf.example`](packaging/montech-hyperflow.conf.example).
Command-line flags always override the file.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | clean exit |
| 1 | transient failure (device gone at startup) |
| 78 | permanent misconfiguration — bad config, no sensor, no permission |

The unit sets `RestartPreventExitStatus=78`, so a misconfiguration fails
visibly instead of restart-looping forever.

## How it works

One 64-byte HID **feature** report per second, report ID `0x07`, on the
`0xFF01` vendor collection of **interface 01**:

```
byte 0    0x07                      report ID
byte 1    hundreds digit            of the displayed number
byte 2    tens digit
byte 3    ones digit
byte 4    (level << 4) | unit       level = min(celsius // 10, 9)
                                    unit: 0 = °C, 1 = °F
byte 5    0 = CPU, 1 = GPU
6..63     0x00
```

Write-only. Nothing to read back, no handshake.

Three things are easy to get wrong here, and all three were wrong in this
project's first draft:

- **The report is 64 bytes, not 65.** The descriptor says `Report Count 0x3F`
  — 63 data bytes plus the report-ID byte. Windows' `HidD_SetFeature` silently
  truncates oversized buffers, which hid the vendor app's own off-by-one;
  Linux does not truncate, so a 65-byte frame is genuinely oversized on the
  wire.
- **In °F mode the digits are Fahrenheit but `level` is still derived from
  Celsius.** The vendor computes it before the conversion. This is preserved
  deliberately. Note also that 93 °C is 199 °F, and the vendor clamps at 199,
  so in °F mode everything at or above 93 °C reads `199`.
- **Do not identify the interface by substring-searching the descriptor.** A
  report descriptor is a self-delimiting item stream; item *data* can spell
  item *headers*. There is a test carrying a descriptor that fools the naive
  check.

## Behaviour worth knowing

- **A failed sensor read never shows `0`.** `0` is a real temperature. The
  driver holds the last good value and then blanks. With no reading ever
  taken, it blanks immediately.
- **Unplug/replug is survivable.** On write failure the daemon closes the fd,
  rediscovers the device — which may come back as a different `hidrawN` — and
  reopens with backoff, re-sending the startup command. `Restart=always` is
  not needed and is not used.
- **The frame length comes from the device**, read out of the report
  descriptor at open time, not from a constant.

## Permissions

The udev rule grants `plugdev` write access to **interface 01 only** and tags
it `uaccess` so the logged-in desktop user gets an ACL too.

Two non-obvious things about it:

- **The filename must sort before `73-seat-late.rules`**, which is where
  systemd invokes the `uaccess` builtin. A `99-` name adds the tag *after* the
  line that consumes it, so `uaccess` is silently ignored. Hence `72-`.
- **`ATTRS{bInterfaceNumber}=="01"` does not work** for narrowing this rule.
  All `ATTRS{}` keys in one rule must match a *single* parent, and
  `idVendor`/`idProduct` live on the USB device node while `bInterfaceNumber`
  lives on the interface node. `ENV{ID_USB_INTERFACE_NUM}` is a property of
  the hidraw device itself and composes freely.

Narrowing matters: this device's **interface 00 is a real boot-keyboard
interface**. Granting group read on its node would let anyone in `plugdev`
read what it reports.

You must be in `plugdev`. If you just added yourself, log out and back in.

## Which package should I use?

| Format | Daemon + udev + service | Tray | Notes |
|---|---|---|---|
| `.deb` / `.rpm` / Arch | ✅ | ✅ | **Recommended.** The only formats that can install a udev rule and a system unit properly. |
| AppImage | ⚠️ tray only | ✅ | Run `montech-hyperflow-setup` once as root for the udev rule; no system service. |
| Flatpak | ❌ | ✅ | Sandboxed; needs `--device=all`, cannot install udev rules or system units. Pair with a native daemon. |
| Snap | ⚠️ | ✅ | `hidraw` interface must be connected manually: `snap connect montech-hyperflow:hidraw`. |

If you just want it to work: install the native package for your distro.

## Troubleshooting

**`--list` finds nothing.** The internal USB header is probably not connected.
`lsusb | grep 1a2c` — the device presents as `SEMICO USB Gaming Keyboard`,
which is a generic OEM string, not a mistake.

**`write failed: [Errno 32] Broken pipe`.** The firmware stalled the control
transfer. Almost always a wrong frame length: check `--list` reports a 64-byte
frame and do not pass `--frame-len`.

**Nothing in the panel.** Check the GNOME AppIndicator extension is enabled,
then run `montech-hyperflow-tray` in a terminal and read the errors.

**The head shows a number, but the wrong one.** Run
`montech-hyperflow --dry-run --test-value 123 --once` and compare the bytes
against the table above before changing anything.

## Development

```bash
make test     # hardware-free unit tests
make lint     # compile, udev rule syntax, desktop file, polkit XML, shell
make deb      # build a package locally
```

Layout:

| Path | What |
|---|---|
| `src/montech_hyperflow/` | the package; `tray/` is the only part importing GTK |
| `tests/` | hardware-free tests |
| `docs/PROTOCOL.md` | the protocol, with the verification status of every claim |
| `docs/EXPERIMENTS.md` | the remaining hardware experiments, each ending in a question |
| `packaging/` | udev, systemd, polkit, icons, and every package format |

## Contributing

Protocol findings are the most valuable contribution. If you run any of the
experiments in `docs/EXPERIMENTS.md`, open an issue with what the head did —
including a negative result. "Sweeping `level` changed nothing on my 360" is
real data.

If you have a rebrand of this pump head that works, say so, and include
`montech-hyperflow --list` output plus your `lsusb` line.

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE) and [`NOTICE.md`](NOTICE.md).

# montech-hyperflow

Drive the 7-segment display on a **Montech HyperFlow Digital 240/360** AIO pump
head from Linux. Montech only ships a Windows app; this speaks the same
protocol over `hidraw`.

Pure Python 3 standard library — no `hidapi`, no `libusb`, no `pyudev`, no
kernel module. The protocol was reverse-engineered from the vendor installer
and then corrected against the device's own HID report descriptor; see
[`PROTOCOL.md`](PROTOCOL.md).

The OEM is TCOMAS and the platform is **DH-C100**, so other rebrands of the
same pump head very likely work unchanged.

## Status

Works: device discovery, frame encoding, the daemon, install and packaging.
The encoder is unit-tested against the disassembly and against the device's
report descriptor.

Not yet answered: what `level` and byte 5 actually *look* like on the head, and
whether the `0xFD` startup command is required. Those need a human looking at
the pump head — see [`EXPERIMENTS.md`](EXPERIMENTS.md).

## Requirements

- Linux with `hidraw` (any modern kernel; `CONFIG_HIDRAW=y`, standard everywhere)
- Python 3.8+
- The cooler's **internal USB header** connected to the motherboard
- For `--source gpu` on NVIDIA: `nvidia-smi` on `$PATH`

## Install

```bash
make test          # hardware-free; should be all green
sudo make install
```

That installs:

| Path | What |
|---|---|
| `/usr/local/bin/montech-hyperflow` | the driver |
| `/etc/udev/rules.d/72-montech-hyperflow.rules` | non-root access + `/dev/montech-hyperflow` |
| `/usr/lib/systemd/system/montech-hyperflow.service` | the daemon |
| `/usr/lib/systemd/system-sleep/montech-hyperflow` | blank on suspend |
| `/usr/local/share/doc/montech-hyperflow/` | these docs |

Then check it found the device:

```bash
montech-hyperflow --list
```

```
matching hidraw nodes (0xFF01 vendor collection):
  /dev/hidraw5  feature report 63 data bytes -> 64-byte frame  stable: /dev/input/by-id/usb-SEMICO_USB_Gaming_Keyboard-if01-hidraw
all 1a2c:4e85 nodes:
  /dev/hidraw4     no vendor collection - do not write to this one  0600 NOT writable by you
  /dev/hidraw5     vendor display                                   0660 writable
cpu sensor: /sys/class/hwmon/hwmon3/temp1_input
            reads 41.0 C  [Package id 0]
```

`hidraw5` must say **writable**. If it does not, see [Permissions](#permissions).

Run it in the foreground first:

```bash
montech-hyperflow --test-value 42        # a fixed, known number
montech-hyperflow                        # live CPU temperature
```

Only once that works:

```bash
sudo make enable
```

Building a package instead:

```bash
make deb          # -> build/montech-hyperflow_0.2.0_all.deb
```

## Usage

```
montech-hyperflow [--source cpu|gpu] [--sensor PATH] [--gpu-index N]
                  [--fahrenheit] [--interval SEC] [--rounding nearest|truncate]
                  [--on-sensor-error hold|blank] [--sensor-error-blank-after SEC]
                  [--device PATH] [--blank-on-exit] [--no-init]
                  [--config PATH] [--list] [--blank] [--once]
                  [--test-value C] [--test-level N] [--frame-len N] [--dry-run]
```

Common things:

```bash
montech-hyperflow --list                       # devices, permissions, sensors
montech-hyperflow --dry-run                    # print frames, write nothing
montech-hyperflow --test-value 123             # hold a known number on the head
montech-hyperflow --blank                      # one-shot clear, then exit
montech-hyperflow --source gpu --gpu-index 1   # second GPU
montech-hyperflow --fahrenheit
```

`--test-value` is always in **Celsius**: it is the encoder's input, so with
`--fahrenheit` the head shows the converted number. It bypasses sensor
selection entirely, so you can test the display on a machine with no working
sensor.

### Configuration file

Instead of flags, put settings in `~/.config/montech-hyperflow.conf` or
`/etc/montech-hyperflow.conf` — see
[`montech-hyperflow.conf.example`](montech-hyperflow.conf.example). Command-line
flags always override the file.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | clean exit |
| 1 | transient failure (device gone at startup) |
| 78 | permanent misconfiguration — bad config, no sensor, no permission |

The unit sets `RestartPreventExitStatus=78`, so a misconfiguration fails
visibly instead of restart-looping forever.

## Permissions

The shipped rule grants `plugdev` write access to **interface 01 only**, and
tags it `uaccess` so the logged-in desktop user gets an ACL too.

Two things about it are easy to get wrong, and both were wrong in the first
draft of this project:

- **The file must sort before `73-seat-late.rules`**, which is where systemd
  invokes the `uaccess` builtin. A `99-*` name adds the tag *after* the line
  that consumes it, so `uaccess` is silently ignored. Hence `72-`.
- **`ATTRS{bInterfaceNumber}=="01"` does not work** for narrowing this rule.
  All `ATTRS{}` keys in one rule must match on a *single* parent, and
  `idVendor`/`idProduct` live on the USB device node while `bInterfaceNumber`
  lives on the interface node. `ENV{ID_USB_INTERFACE_NUM}` is a property of the
  hidraw device itself and composes freely.

Narrowing matters here: this device's **interface 00 is a real boot-keyboard
interface**. Granting group read on its `hidraw` node would let anyone in
`plugdev` read whatever it reports.

You must be in `plugdev` (`id -nG | grep plugdev`). If you just added yourself,
log out and back in. Re-applying the rule to an already-created node needs an
explicit trigger, which `make install` does:

```bash
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=hidraw --action=add
```

## How it works

One 64-byte HID **feature** report per second, report ID `0x07`:

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

Write-only; there is nothing to read back and no handshake.

**A vendor quirk this driver deliberately preserves:** in °F mode the digits
are the Fahrenheit value but `level` is still derived from **Celsius**, because
the vendor computes it before the unit conversion. Do not "fix" it without
hardware evidence.

Note that 93 °C converts to 199 °F, and the vendor clamps the displayed value
at 199 — so in °F mode everything at or above 93 °C reads `199`.

## Behaviour worth knowing

- **A failed sensor read never shows `0`.** `0` is a real temperature. The
  driver holds the last good value (`--on-sensor-error hold`, the default) and
  blanks after 30 s (`--sensor-error-blank-after`). With no reading ever taken,
  it blanks immediately.
- **Unplug/replug is survivable.** On a write failure the daemon closes the fd,
  rediscovers the device — which may come back as a different `hidrawN` — and
  reopens with exponential backoff up to 30 s, re-sending the startup command.
  `Restart=always` is not needed and is not used.
- **`hidrawN` is not stable.** Discovery walks each candidate's report
  descriptor and picks the one that actually declares the `0xFF01` display
  collection. The udev rule also creates a stable `/dev/montech-hyperflow`.
- **The frame length comes from the device**, not from a constant: the driver
  reads `Report Count` out of the report descriptor at open time. If a future
  firmware revision changes it, the driver follows and says so.

## Troubleshooting

**`--list` finds nothing.** The internal USB header is probably not connected.
Check with `lsusb | grep 1a2c` — the device presents as `SEMICO USB Gaming
Keyboard`, which is a generic OEM string, not a mistake.

**`permission denied on /dev/hidrawN`.** See [Permissions](#permissions).

**`write failed: [Errno 32] Broken pipe`.** The firmware stalled the control
transfer. Almost always a wrong frame length — check `--list` shows a 64-byte
frame, and do not pass `--frame-len`.

**The head shows something, but not the right number.** Run
`montech-hyperflow --dry-run --test-value 123 --once` and compare the bytes
against the table above before changing anything.

## Development

```bash
make test     # 41 tests, no hardware needed
make lint     # compile, udev rule syntax, shell syntax
```

The tests cover the frame encoder against every claim in `PROTOCOL.md`
(including the preserved Fahrenheit asymmetry and the 199 clamp), the HID
descriptor parser against synthetic edge cases, config parsing and precedence,
and the sensor-failure policy.

## Files

| File | What |
|---|---|
| `montech-hyperflow.py` | the driver |
| `test_montech.py` | hardware-free tests |
| `PROTOCOL.md` | reverse-engineering notes, with `DeviceDriver.exe` addresses, and the verification status of every claim |
| `EXPERIMENTS.md` | the Phase 2 hardware experiments, each ending in a question for a human |
| `72-montech-hyperflow.rules` | udev rule |
| `montech-hyperflow.service` | systemd unit |
| `montech-hyperflow-sleep` | systemd-sleep hook |
| `montech-hyperflow.conf.example` | annotated config template |

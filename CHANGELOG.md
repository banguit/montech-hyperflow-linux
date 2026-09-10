# Changelog

All notable changes to montech-hyperflow are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

One project-specific convention: entries say whether a protocol claim was
**confirmed on hardware** or only inferred. The authoritative record is the
confidence table in [`docs/PROTOCOL.md`](docs/PROTOCOL.md); this file only
tracks when an item moved between tables.

## [Unreleased]

Nothing yet.

## [0.3.0] - 2026-09-10

The release that turned a single script into something installable, and the
first release with anything confirmed against the real pump head.

### Confirmed on hardware

- **The head displays what the driver sends.** `--test-value 123` produced
  `123` on the display. That single observation confirms the 64-byte frame
  length on the wire, feature report `0x07` on the `0xFF01` collection of
  interface 01, the hundreds/tens/ones digit order, that the digits are plain
  decimal and not BCD, and that the `0xFD` startup frame does not blank the
  display.
- **Live tracking works.** A 20-thread stress run took the CPU from 41 °C to a
  59 °C plateau and back to 43 °C, and the head followed to the degree —
  confirming sensor autodetection (`coretemp`, *Package id 0*), the 1 Hz
  cadence and the daemon's steady-state path. The run only spanned `level`
  4→5, so it is **not** evidence about the level ramp; E2 in
  [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md) still needs a deliberate sweep.

### Added

- The `montech_hyperflow` package: `hid`, `frame`, `device`, `sensors`,
  `config`, `status`, `daemon`, `cli`, `admin`. The daemon half is standard
  library only, on purpose — no hidapi, no libusb, no pyudev, no D-Bus binding.
- **Panel indicator** (`montech-hyperflow-tray`), GTK 3 plus
  AyatanaAppIndicator3, living entirely in `montech_hyperflow.tray` so a
  headless install never pulls in GTK. It is a client and never opens the
  device: two writers to one display would fight at 1 Hz.
- **Status file.** The daemon writes `/run/montech-hyperflow/status.json`
  atomically each tick; the tray reads it and `montech-hyperflow --status`
  prints it. No IPC protocol to get wrong.
- **`montech-hyperflow-admin`**, a small `pkexec` helper that validates every
  key before writing `/etc/montech-hyperflow.conf`, with a polkit policy.
- Original icons (scalable and symbolic), a desktop entry and an autostart
  entry. No vendor artwork, here or anywhere.
- Portable install to a private libdir (`/usr/lib/montech-hyperflow`) with
  generated wrappers that strip the working directory from `sys.path` — the
  admin helper runs as root under `pkexec`, where an inherited cwd containing
  a shadowing module would be an escalation path.
- Packaging for Debian/Ubuntu, Fedora, Arch, Flatpak and AppImage, an
  AppStream metainfo file, and GitHub Actions that build the `.deb`, assert
  the installed layout file by file, and validate the udev rule, desktop
  entry, polkit policy and metainfo.
- `LICENSE` (GPL-3.0-or-later) and [`NOTICE.md`](NOTICE.md), which states
  plainly that this is unofficial and not Montech software.
- A hardware-free unit test suite, run on Python 3.8 through 3.13.

### Changed

- Documentation moved into `docs/` (`PROTOCOL.md`, `EXPERIMENTS.md`,
  `KICKOFF.md`) and the README was rewritten around installation rather than
  around the reverse engineering.

## [0.2.0] - 2026-09-09

The report length was wrong, and so was everything that followed from it.

### Fixed

- **The report is 64 bytes, not 65.** The device's own report descriptor
  declares `Report Count 0x3F` for feature report 7 — 63 data bytes plus the
  report-ID byte. The vendor app's 65-byte buffers were silently truncated by
  Windows' `HidD_SetFeature`; Linux does not truncate, so the previous driver
  was putting an oversized `SET_REPORT` on a low-speed control pipe. The frame
  length is now read from the descriptor at open time rather than hardcoded.
- **Interface detection no longer substring-matches the report descriptor.** A
  descriptor is a self-delimiting item stream in which item *data* can spell
  item *headers*; the naive check could pick the wrong interface. Replaced
  with a real descriptor walker, and there is a test carrying a descriptor
  that fools the old approach.
- **A failed sensor read never displays `0`** — `0` is a real temperature. The
  driver holds the last good value and then blanks.
- **The udev rule now works.** Renamed `99-` to `72-`: `TAG+="uaccess"` set by
  a file that sorts after `73-seat-late.rules` is added after the line that
  consumes it and is silently ignored. The rule is also scoped to
  `ENV{ID_USB_INTERFACE_NUM}=="01"`, so the boot-keyboard interface — a
  keystroke-capture risk — is not exposed.
- **Every service stop no longer took 90 seconds.** `ExecStopPost=` ran the
  driver a second time with `--blank-on-exit`, which only acts after the main
  loop ends; the "cleanup" process re-sent the init frame and streamed
  temperatures until systemd's stop timeout killed it.

### Added

- Unplug and re-enumeration are survivable: on write failure the daemon closes
  the fd, rediscovers the device — which may come back as a different
  `hidrawN` — and reopens with backoff, re-sending the startup command.
- Experiment flags: `--test-value`, `--test-level`, `--frame-len`, `--blank`,
  `--once`, `--dry-run`.
- `--gpu-index`, a configuration file, rounding control, and exit code 78
  (`EX_CONFIG`) for permanent misconfiguration, which the unit pairs with
  `RestartPreventExitStatus=78` so a bad config fails visibly instead of
  restart-looping.
- A systemd-sleep hook that blanks the head before suspend. It is a
  `system-sleep` hook rather than a unit wanted by `sleep.target`, because on
  many systems that target is masked and such a unit is silently never run.
- `Makefile`, `README.md`, `EXPERIMENTS.md` and the first tests.

### Changed

- The unit uses `Type=exec` (with `Type=simple`, a missing executable becomes
  a silent restart loop), `DynamicUser=yes` with `SupplementaryGroups=plugdev`,
  and a hardening block. `After=multi-user.target` was dropped: combined with
  `WantedBy=multi-user.target` it pushed the service to the very end of boot.
- Signal handlers are installed before the device is opened.

### Removed

- The `0 °C → "0"` special case in Fahrenheit mode; the vendor shows `32`.
- The `99-montech-hyperflow.rules` file, replaced by the `72-` one.

## [0.1.0] - 2026-09-09

Initial import of the reverse-engineering starting point: the driver script,
the protocol notes, a udev rule and a systemd unit.

**Nothing in this version had ever been written to real hardware.** Its report
length, its interface detection and its udev rule were all subsequently found
to be wrong; it is recorded here only so the later corrections have something
to refer to.

<!-- 0.1.0 and 0.2.0 predate tagging and have no release page. -->
[Unreleased]: https://github.com/OWNER/montech-hyperflow/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/OWNER/montech-hyperflow/releases/tag/v0.3.0

# Claude Code kickoff prompt — Montech HyperFlow Digital Linux driver

Save this as `KICKOFF.md` in a fresh repo alongside the four files listed under
"Starting point", then paste the whole thing as your first message to Claude
Code. Fill in the two `<<< >>>` placeholders first.

---

## Task

I have a Montech HyperFlow Digital 240 AIO. Its pump head has a 7-segment
display that only works with Montech's Windows app. I reverse-engineered the
Windows installer and have a working protocol spec plus an untested Python
driver. Your job is to get it actually working on this machine, verify the
parts that are still guesses, and package it properly.

**This machine has the cooler physically installed.** Everything device-facing
in the current code was written in a sandbox with no hardware, so treat it as a
plausible first draft, not as known-good.

## System

- Distro / kernel: <<< fill in, or run `uname -a` and `lsb_release -a` >>>
- CPU / GPU: <<< fill in >>>

## Starting point

Files in this repo:

- `montech-hyperflow.py` — the driver. Pure stdlib, talks to `/dev/hidrawN`
  via `HIDIOCSFEATURE`. Has `--list`, `--dry-run`, `--blank-on-exit`.
- `PROTOCOL.md` — the reverse-engineering notes, with addresses in
  `DeviceDriver.exe` for every claim.
- `99-montech-hyperflow.rules` — udev rule.
- `montech-hyperflow.service` — systemd unit.

The frame encoder has been unit-tested against the disassembly. The main loop
runs against a fake sensor file. Nothing has ever been written to real hardware.

## Protocol summary

Recovered from `HyperFlow Digital setup 1.0.1.5.exe` → `app/DeviceDriver.exe`
(PE32, MFC, statically linked hidapi). The OEM is TCOMAS; the platform is
called DH-C100. Montech is a rebrand.

- USB `1a2c:4e85`, vendor HID collection UsagePage `0xFF01` / Usage `0x01`.
- HID **feature** report (`HidD_SetFeature` on Windows → `HIDIOCSFEATURE` here).
- 65 bytes: 1 report-ID byte + 64 payload. Sent once per second.
- Write-only. `HidD_GetFeature` is resolved but never called, so there is
  nothing to read back and no handshake to wait for.

Frame layout:

```
byte 0 : 0x07                      report ID
byte 1 : hundreds digit            of the displayed number
byte 2 : tens digit
byte 3 : ones digit
byte 4 : (level << 4) | unit       level = min(celsius // 10, 9)
                                   unit: 0 = degC, 1 = degF
byte 5 : 0 = CPU, 1 = GPU
6..64  : 0x00
```

Other frames:

- Startup command: `07 FD 00 00 ...`
- Blank display: `07 00 00 00 ...` (all-zero payload)

Deliberate quirk to preserve: in Fahrenheit mode the digits are the Fahrenheit
value, but `level` is still derived from Celsius. The vendor code computes
`level` before the unit conversion. Do not "fix" this unless hardware testing
proves it wrong.

## Confidence levels

Read `PROTOCOL.md` for the addresses backing each of these.

**Verified by disassembly** — treat as fact unless hardware contradicts it:

- VID/PID and the `0xFF01`/`0x01` collection match.
- Feature report, ID `0x07`, digits as plain decimal bytes, 1 Hz cadence.
- Byte 4 packing and the Celsius-derived `level`.
- The `0xFD` startup command and the all-zero blank frame.
- No checksum, no sequence counter, no BCD.

**Inferred, needs hardware confirmation:**

- That byte 5 does anything visible on a 7-segment head. The vendor app sets
  it from its CPU/GPU toggle, but the head may ignore it.
- What `level` actually does. I assume a colour or intensity ramp.
- Whether the startup `0xFD` command is required at all, or just an identify
  ping the display tolerates being skipped.
- The 65 vs 64 length question. The vendor app sends temperature frames as 65
  bytes but the startup command as 64 — probably an off-by-one on their side.
  The driver sends 65 for both.

**Pure guesswork in the current code:**

- Sensor autodetection ordering (`k10temp` Tctl, `coretemp` Package id 0, …).
- That `TAG+="uaccess"` in the udev rule behaves the way I expect here.

## Rules of engagement

1. **You cannot see the display. I am your only oracle for what it shows.**
   After any change that could alter the output, stop and ask me what the head
   is displaying. Do not chain several device-affecting changes together and
   then ask once.
2. Run the driver in the foreground during development. Do not install the
   systemd unit until the foreground path is confirmed working.
3. **Do not fuzz command bytes.** Byte 1 values of 2 and above are out-of-band
   commands, and `0xFD` is the only one I've identified. Others may reach
   firmware-update or factory paths. If you want to probe, propose the exact
   bytes and wait for me to approve.
4. Never invent a VID/PID. If `--list` finds nothing, gather evidence
   (`lsusb`, `ls /sys/class/hidraw/`, the `uevent` and `report_descriptor`
   files) and show it to me before changing the matching logic.
5. When you deviate from `PROTOCOL.md`, say which claim you're contradicting
   and what evidence you have. Update `PROTOCOL.md` in the same commit.
6. Prefer stdlib. Do not add hidapi, libusb, or pyudev unless something
   genuinely can't be done without them, and say why first.

## Phase 1 — get it displaying something

1. Confirm the device enumerates: `lsusb`, then `./montech-hyperflow.py --list`.
   If the hidraw node is found but the vendor-collection filter rejects it,
   show me the parsed report descriptor before relaxing the filter.
2. Install the udev rule and confirm non-root write access.
3. Run in the foreground with a fixed value first, not live temperatures — add
   a `--test-value N` flag so we get a deterministic, known number on the head.
   Ask me what it shows.
4. Only once digits are correct, switch to the live sensor and confirm the
   number tracks a load (`stress-ng` or a build) sensibly against `sensors`.

## Phase 2 — resolve the open questions

Design a small experiment for each, run it one at a time, ask me what changed:

- Does byte 5 = 1 alter anything?
- What does `level` control? Sweep 0..9 with the digits held constant.
- Does skipping the `0xFD` startup command break anything after a cold boot?
- Does a 64-byte frame work as well as 65?
- What happens on suspend/resume and on a monitor-off cycle — does the head
  need re-initialising?

Record every answer in `PROTOCOL.md`, promoting items out of the "inferred"
section as they're confirmed.

## Phase 3 — make it solid

- Handle device unplug/replug and USB re-enumeration without dying. The
  systemd `Restart=always` is a crutch; the daemon should reopen the device.
- Sensible behaviour when the sensor read fails: hold the last value or blank,
  not display 0.
- Blank the display cleanly on shutdown and on suspend.
- A `--config` file so the source, unit, and interval aren't only CLI flags.
- Tests for the encoder that don't need hardware, kept passing.
- A real README with install instructions.
- Packaging: at minimum a `make install`. A PKGBUILD or .deb if it's cheap.

## Acceptance

The head shows live CPU temperature, correct to the degree against `sensors`,
survives a reboot and a suspend/resume cycle, runs as a non-root systemd
service, and `PROTOCOL.md` has no unresolved items in the "inferred" section.

## Optional

If you want to re-verify anything against the original binary, the installer is
at <<< path to HyperFlow_Digital_setup_1_0_1_5.zip, or delete this section >>>.
It's Inno Setup 6.4.0.1, so Ubuntu's packaged `innoextract` 1.9 is too old —
build innoextract from git master, which handles 6.4.0.1. The interesting
functions are `fcn.00408db0` (builds the temperature frame), `fcn.0040aff0`
(startup command), `fcn.0040c410` (the `HidD_SetFeature` wrapper), and
`fcn.00405d80` (device validation).

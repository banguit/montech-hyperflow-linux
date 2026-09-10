# Montech HyperFlow Digital — display protocol

Recovered from `HyperFlow Digital setup 1.0.1.5.exe` (Inno Setup 6.4.0.1,
payload `app/DeviceDriver.exe`, PE32 / MFC / static hidapi).

Addresses below are from `DeviceDriver.exe` at its default image base
`0x00400000`, so you can re-check any of this yourself.

## Device identity

| | |
|---|---|
| USB VID:PID | `1a2c:4e85` |
| Match method | vendor app uppercases the device-interface path and does `wcsstr` for `VID_1A2C&PID_4E85` (`fcn.00405bd0` @ `0x00405c81`) |
| HID collection | UsagePage `0xFF01`, Usage `0x01` (`fcn.00405d80` @ `0x00405dff`/`0x00405e0b`) |
| Transport | HID **feature** report via `HidD_SetFeature` (`fcn.0040c410` @ `0x0040c429`) |
| Report length | 64 bytes = 1 report-ID byte + 63 payload — **corrected on-device**, see [Report length](#report-length-corrected) |
| Cadence | `Sleep(1000)` between frames (`0x00408faf`) |

VID `0x1A2C` is a generic Chinese HID vendor ID. The UI resources name the OEM
as **TCOMAS** and the platform as **DH-C100**, so other rebrands of the same
pump head very likely speak this protocol unchanged.

`HidD_GetFeature` is resolved at load time but never called — the display is
write-only. There is no handshake to wait for and nothing to read back.

<a name="report-length-corrected"></a>
### Report length — corrected against the hardware

The device's own report descriptor settles this; it is no longer an inference.
Read back from `/sys/class/hidraw/hidraw5/device/report_descriptor` on a real
HyperFlow Digital 240 (interface 1 of `1a2c:4e85`), parsed with an item walker:

```
06 01 FF     Usage Page (Vendor 0xFF01)
09 01        Usage (0x01)
A1 01        Collection (Application)
85 07          Report ID (7)
09 03          Usage (0x03)
15 00          Logical Minimum (0)
26 FF 00       Logical Maximum (255)
75 08          Report Size (8)
95 3F          Report Count (63)          <-- 63, not 64
B1 02          Feature (Data,Var,Abs)
09 04          Usage (0x04)
15 00 26 FF 00 75 08 95 3F
91 02          Output (Data,Var,Abs)      <-- a second, unused path
C0           End Collection
```

`Report Count 0x3F` = 63 data bytes. With the report-ID byte that
`HIDIOCSFEATURE` expects in `buf[0]`, the buffer is **64 bytes**, and the
control transfer carries `wValue=0x0307`, `wIndex=1`, `wLength=64`.

**This means the vendor app never sent 65 bytes.** Windows `HidD_SetFeature`
silently truncates a buffer longer than `caps.FeatureReportByteLength` — hidapi
documents exactly this in `hid_send_feature_report()` — and for this descriptor
that length is 64. So the app's 65-byte temperature buffers went out as 64 on
the wire, identical to its 64-byte `0xFD` startup frame. There was no
off-by-one in the vendor code at all; the off-by-one was in these notes.

Linux does not truncate. `hidraw_send_report()` takes the length from
`_IOC_SIZE(cmd)` verbatim and `usbhid_set_raw_report()` passes it straight to
`usb_control_msg()`; nothing between them clamps it against the parsed report
length. `HIDIOCSFEATURE(65)` therefore puts a genuinely oversized SET_REPORT on
a low-speed (`bMaxPacketSize0 = 8`) control pipe.

The descriptor also declares an **Output** instance of report 7, same 63 bytes.
The vendor app does not use it. It is an untested alternative path.

## Frame layout

Report ID `0x07`, then:

| Offset | Meaning |
|---|---|
| 0 | `0x07` — report ID |
| 1 | hundreds digit of the displayed number |
| 2 | tens digit |
| 3 | ones digit |
| 4 | `(level << 4) | unit` |
| 5 | source: `0` = CPU, `1` = GPU |
| 6–63 | zero |

- `level` = `min(celsius // 10, 9)`, clamped to one nibble. It drives the
  colour/intensity ramp on the head.
- `unit`: `0` = °C, `1` = °F.
- Digits are the *displayed* value, so in °F mode they are the Fahrenheit
  number — but **`level` is still computed from Celsius**. That asymmetry is in
  the original (`var_50h`/`var_54h` are computed at `0x00408dfe`–`0x00408e3b`,
  before the °F conversion at `0x00408e4a`), so don't "fix" it.
- The vendor app clamps the displayed value at 199 (`0x00408e9f`).

Digit extraction in the original is the usual compiler-emitted
multiply-high-and-shift sequence (`0x51eb851f` for ÷100, `0x66666667` for ÷10)
at `0x00408ee4`–`0x00408f7b`. It is plain decimal splitting, nothing exotic:
no BCD packing, no checksum, no sequence counter.

## Commands

**Startup / identify** (`fcn.0040aff0` @ `0x0040b078`):

```
07 FD 00 00 ... 00
```

Byte 1 = `0xFD`. Since a real hundreds digit is only ever 0 or 1, values ≥ 2 in
that position act as out-of-band commands. The vendor app sends this once at
startup, then polls with `Sleep(16)` retries.

~~Quirk: this frame is sent with length `0x40` (64) while temperature frames use
`0x41` (65). Almost certainly an off-by-one in the vendor code. Sending 65 for
both works.~~

**Retracted.** The `0x40` was correct and the `0x41` was harmless: see
[Report length](#report-length-corrected). `HidD_SetFeature` truncated the
oversized temperature buffers to the descriptor's 64 bytes, so both frames were
64 bytes on the wire. “Sending 65 for both works” had no evidence behind it and
is wrong on Linux, where nothing truncates.

**Blank the display** (`0x00408f8b`): all-zero payload, i.e. `07 00 00 ...`.
Emitted when the display-enable setting is off.

## Sensor plumbing in the vendor app (for reference only)

`fcn.00408db0` pulls three floats out of a monitoring object at `[this+0x7a0]`:
`+0x98` (CPU), `+0x128` and `+0x1bc` (two GPU candidates — the first non-zero
one wins). Those come from `api-ms-win-core-sysinfo-835-{32,64}.dll`, which
despite the name is not a Windows API set stub; it is the OEM's bundled
hardware-monitoring library (the 32-bit copy is UPX-packed). None of this
matters on Linux — you read hwmon instead.

## Settings observed

| Field | Meaning |
|---|---|
| `[this+0x3b0]` | display source: 0 = CPU, 1 = GPU (also copied to byte 5) |
| `[this+0x3b4]` | unit: 0 = °C, 1 = °F (low nibble of byte 4) |
| `[this+0x34c]` | display enable; when not 1, the blank frame is sent |

Persisted in the registry via `RegSetValueExW`, not an INI.

## Linux notes

`HidD_SetFeature` maps to `ioctl(fd, HIDIOCSFEATURE(64), buf)` with
`buf[0] = 0x07`. There is no need for hidapi or libusb, and no kernel driver
needs to be unbound — hidraw is sufficient because this is a vendor-defined
collection, not an input device.

The pump head exposes more than one HID interface. Match on the report
descriptor declaring a top-level application collection with Usage Page
`0xFF01` / Usage `0x01`, which is what the vendor app checks via
`HidP_GetCaps`.

Do **not** do this by searching the descriptor for the byte substrings
`06 01 FF` and `09 01`. A report descriptor is a self-delimiting item stream,
so the *data* bytes of one item can spell the header of another: a four-byte
Logical Maximum of `0xFF0106FF` contains `06 01 FF` while declaring no vendor
page, and `09 01` is Usage(1), present in most descriptors. Walk the items.
`test_montech.py::DescriptorParser::test_substring_heuristic_is_unsound`
carries a descriptor that defeats the substring test.

### What the two interfaces are

| Interface | Protocol | Node here | Contents |
|---|---|---|---|
| 00 | boot keyboard (`bInterfaceProtocol 01`) | `/dev/hidraw4` | 8-byte keyboard input, LED output. No vendor collection. Backs a real keyboard input node — **never write to it, and do not loosen its permissions**. |
| 01 | report protocol (`bInterfaceProtocol 02`) | `/dev/hidraw5` | Consumer, System Control, three keyboard collections, a vendor `0xFF00` collection, and the `0xFF01` display collection. |

The device's USB strings are `SEMICO` / `USB Gaming Keyboard` — generic OEM
leftovers from the SEMICO HID controller, not a sign you have the wrong device.
`hidrawN` numbering is not stable across replug; the shipped udev rule creates
`/dev/montech-hyperflow` for interface 01.

## Verification status

Confidence for every claim above. Items move out of **Inferred** only when the
pump head itself has been observed; see `EXPERIMENTS.md` for the experiment
that settles each one.

### Confirmed on hardware

Observed on a Montech HyperFlow Digital 240 (2026-09-10), oracle = the pump
head itself. Command:
`montech-hyperflow --device /dev/montech-hyperflow --test-value 123`
sending `07 FD 00 …` once, then `07 01 02 03 90 00 …` at 1 Hz.
**The head displayed `123`.** That single observation settles five things:

- The **64-byte** feature report reaches the firmware and is acted on. The
  descriptor-derived length is not merely descriptor-correct, it works.
- Report ID `0x07` on the `0xFF01` collection of **interface 01** is the
  display path, over `HIDIOCSFEATURE` — no hidapi, no libusb, no unbinding.
- **Digit order is byte 1 = hundreds, byte 2 = tens, byte 3 = ones**, exactly
  as the disassembly said. Not reversed, not rotated.
- Digits are **plain decimal bytes**. `01 02 03` rendered as `123`; BCD would
  have produced something else entirely.
- The `0xFD` startup frame **does not blank or disturb** the display when
  followed by a temperature frame.

Access path also confirmed end-to-end: the `72-` udev rule gives `/dev/hidraw5`
both `GROUP=plugdev` and a `uaccess` ACL for the seat user, while
`/dev/hidraw4` — the boot-keyboard interface — stays `root:root 0600`.

**Live tracking confirmed too.** With the daemon on the autodetected sensor
(`coretemp` / `Package id 0`), a 20-thread `stress` run drove the package from
41 °C to a 59 °C plateau and back to 43 °C, and the head followed it to the
degree against `sensors`. Sensor plumbing, the 1 Hz cadence and the daemon's
steady-state path are all good.

That run only spanned `level` 4 → 5, so it says nothing conclusive about the
level ramp; E2 still has to sweep it deliberately.

Still open: everything that is not a digit. See the *Inferred* table below.

### Verified — by disassembly *and* by the device's report descriptor

- USB `1a2c:4e85`; vendor collection UsagePage `0xFF01` / Usage `0x01` on
  interface **01**. Confirmed present on hardware.
- HID **feature** report, report ID `0x07`.
- **Report length is 64 bytes** (1 + 63). Settled from `Report Count 0x3F` in
  the device's descriptor, and since confirmed on hardware — a 64-byte frame
  is accepted and displayed. This *replaces* the earlier 65-byte claim and
  closes the "65 vs 64" open question.
  The vendor's `0x40`/`0x41` asymmetry was a Windows truncation artefact, not
  a protocol fact.

### Verified — by disassembly only

- ~~Digits are plain decimal bytes; no BCD, no checksum, no sequence
  counter.~~ **Promoted: confirmed on hardware.**
- Byte 4 packing `(level << 4) | unit`, with `level = min(celsius // 10, 9)`
  computed **before** any °F conversion.
- The displayed value is clamped at 199.
- The `0xFD` startup command and the all-zero blank frame.
- 1 Hz cadence (`Sleep(1000)`).

### Inferred — needs the pump head as oracle

| # | Question | Experiment |
|---|---|---|
| 1 | Does byte 5 (`0` CPU / `1` GPU) change anything visible on a 7-segment head? | `E1` |
| 2 | What does `level` actually drive — colour, brightness, nothing? | `E2` |
| 3 | Is the `0xFD` startup command *required*? (Known: it does not blank or disturb the display when sent.) | `E3` |
| 4 | Does the descriptor-*incorrect* 65-byte frame also work, or does the firmware stall it? (64 is now known to work.) | `E4` |
| 5 | Does the head need re-initialising after suspend/resume or a monitor-off cycle? | `E5` |
| 6 | Does the unit nibble visibly change anything (a °C/°F indicator segment)? | `E-Unit` |
| 7 | Does the declared Output instance of report 7 work as well as Feature? | `E6` |

### Corrected — claims from the original notes that the hardware contradicts

| Claim | Status |
|---|---|
| "65 bytes = 1 + 64 payload" | **Wrong.** 64 = 1 + 63, from the descriptor. |
| "sending 65 for both works" | **Unevidenced and wrong on Linux.** Windows truncated; Linux does not. |
| "the vendor's 0x40 startup length is an off-by-one" | **Backwards.** `0x40` was the correct length. |
| match the descriptor with the substrings `06 01 FF` / `09 01` | **Unsound.** Item data can spell item headers; walk the descriptor. |

### Driver-side deviations from the vendor app

Deliberate, and none of them change bytes on the wire for a normal reading:

- **°F at 0 °C shows `032`, not `000`.** The first Linux draft special-cased
  `celsius == 0` to display `0`. The vendor does not, and it made a genuine
  0 °C reading indistinguishable from a dead sensor. Removed.
- **A failed sensor read never displays `0`.** `0` is a real temperature. The
  driver holds the last good value, then blanks. The vendor app has no
  equivalent situation because it reads through its own monitoring DLL.
- **Millidegrees are rounded, not truncated,** by default (`--rounding`), so
  the head agrees with `sensors`. The vendor app truncates; pass
  `--rounding truncate` to match it exactly.

Note for anyone reading a °F experiment result: 93 °C converts to 199 °F, so
**every value at or above 93 °C shows `199` in °F mode**. That is the vendor's
own clamp, not a driver bug.

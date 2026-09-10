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
| Report length | 65 bytes = 1 report-ID byte + 64 payload |
| Cadence | `Sleep(1000)` between frames (`0x00408faf`) |

VID `0x1A2C` is a generic Chinese HID vendor ID. The UI resources name the OEM
as **TCOMAS** and the platform as **DH-C100**, so other rebrands of the same
pump head very likely speak this protocol unchanged.

`HidD_GetFeature` is resolved at load time but never called — the display is
write-only. There is no handshake to wait for and nothing to read back.

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
| 6–64 | zero |

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

Quirk: this frame is sent with length `0x40` (64) while temperature frames use
`0x41` (65). Almost certainly an off-by-one in the vendor code. Sending 65 for
both works.

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

`HidD_SetFeature` maps to `ioctl(fd, HIDIOCSFEATURE(65), buf)` with
`buf[0] = 0x07`. There is no need for hidapi or libusb, and no kernel driver
needs to be unbound — hidraw is sufficient because this is a vendor-defined
collection, not an input device.

The pump head exposes more than one HID interface. Match on the report
descriptor containing `06 01 FF` (Usage Page 0xFF01) and `09 01` (Usage 1),
which is what the vendor app checks via `HidP_GetCaps`.

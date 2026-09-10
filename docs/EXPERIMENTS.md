# Phase 2 — hardware experiments

**You are the only oracle.** Nothing here is self-verifying: the driver cannot
see the pump head. Every step that changes what the head could be showing ends
with a **STOP** and one question.

## Ground rules

1. **One variable per step.** Digits are held constant wherever the variable
   under test is not the digits, so any change is attributable.
2. **Never chain two device-affecting steps and then ask once.**
2b. **Stop the daemon first.** If `montech-hyperflow.service` is running it
   rewrites the display every second, so a `--once` test frame is gone before
   you can look at it. Every experiment below assumes:
   ```bash
   sudo systemctl stop montech-hyperflow
   ```
   and a `sudo systemctl start montech-hyperflow` when you are done. The
   driver refuses a test frame while another writer is live, so you cannot
   get this wrong silently -- but it is easier to stop it up front than to
   read the error.
3. **Pin the device.** `hidrawN` numbering moves across replug and resume. Use
   the stable symlink the udev rule creates:
   ```bash
   ls -l /dev/montech-hyperflow
   ```
   Every command below adds `--device /dev/montech-hyperflow` so autodetection
   can never silently pick a different node mid-experiment.
4. **Never write to `/dev/hidraw4`.** That is interface 00, the boot-keyboard
   interface. It has no display collection.
5. **Do not fuzz byte 1.** Values ≥ 2 there are out-of-band commands and only
   `0xFD` is identified. Every frame below keeps byte 1 as a hundreds digit
   (0 or 1) except `E3`, which uses the known `0xFD`. Anything else needs your
   explicit approval of the exact bytes first.
6. Check the frame before it goes out. Every command has a `--dry-run` twin;
   run that first if you want to see the bytes.

## Reference: reading a frame

```
07  01 02 03  90  00  00 ...
|   |  |  |   |   |
|   |  |  |   |   +-- byte 5: source, 0 = CPU, 1 = GPU
|   |  |  |   +------ byte 4: (level << 4) | unit    0x90 = level 9, °C
|   +--+--+---------- bytes 1-3: hundreds, tens, ones  -> "123"
+-------------------- report ID 0x07
```

---

## E0 — first contact

**Purpose:** confirm the 64-byte frame reaches the firmware at all, and that
digit order is what `PROTOCOL.md` claims.

`123` is chosen because all three digits differ, so a transposed or
reversed digit order is unmistakable.

```bash
montech-hyperflow --device /dev/montech-hyperflow --dry-run --test-value 123 --once
```

Expect `init  -> 07 FD ...` then `07 01 02 03 90 00 ...`. Then, for real:

```bash
montech-hyperflow --device /dev/montech-hyperflow --test-value 123
```

Leave it running.

> **STOP — what is on the head right now?**
> Specifically: what three characters, in what order, and is anything else
> different about it (colour, brightness, an extra symbol)?

| Answer | Conclusion |
|---|---|
| `123` | Frame layout, digit order and 64-byte length all confirmed. |
| `321` | Digit order is reversed; swap bytes 1 and 3. |
| `23` or `1 2` | Leading-zero/segment-blanking behaviour differs; note it. |
| unchanged from before | Frame is not reaching the display. Check stderr for `Broken pipe`; go to `E4` early. |
| blank | The `0xFD` init may be blanking; retry with `--no-init`. |

### E0.1 — does it latch?

With the above still running, stop it with Ctrl-C (no `--blank-on-exit`, so
nothing is sent on the way out) and wait 60 s.

> **STOP — is the head still showing the same thing, or did it blank/change on
> its own?**

This tells us whether the display latches the last frame or has a watchdog that
clears it. It decides whether Phase 3 needs a keepalive when the value is
unchanged.

### E0.2 — the blank frame

```bash
montech-hyperflow --device /dev/montech-hyperflow --blank
```

> **STOP — did the head go blank/dark, or does it show `000`?**

`000` rather than blank would mean the all-zero payload is just "zero degrees"
and there is no blank command — which changes the whole shutdown story.

---

## E-Unit — the unit nibble (cheap, do it early)

**Variable:** byte 4 low nibble, `0` → `1`. Digits change too (that is
unavoidable — °F *is* a different number), so pick a value where the two are
unambiguous.

50 °C → 122 °F. Both are three digits, and they share no digit in the same
position.

```bash
montech-hyperflow --device /dev/montech-hyperflow --test-value 50 --once     # 07 00 05 00 50 00
montech-hyperflow --device /dev/montech-hyperflow --test-value 50 --fahrenheit --once  # 07 01 02 02 51 00
```

Run the first, look, then the second, look.

> **STOP — for each of the two, what did the head show, and did the unit
> indicator change?** The manual's render shows a small green `°C` at the
> bottom right of the display, so the expected change is `°C` → `°F`.

| Answer | Conclusion |
|---|---|
| `50` then `122`, nothing else changes | The nibble is passed through but the head has no unit indicator. Promote item 6 to "no visible effect". |
| a unit symbol appears/changes | The head has a °C/°F indicator driven by the nibble. Document which segment. |
| `122` shows wrong or blank | Three digits ≥ 100 may render differently; note it. |

Note the level stayed `5` in **both** frames (`0x50` and `0x51`) — that is the
deliberate vendor asymmetry, level from Celsius. If the head's colour changes
between these two frames, the asymmetry is observable and worth recording.

---

> **Prior from the vendor manual (read `docs/PROTOCOL.md` first).** The
> official manual's render of the head shows a `CPU` text label, a
> 10-segment vertical bar, white digits and a `°C` indicator. So E1, E2 and
> E-Unit all have a strongly expected answer now. That does not make them
> redundant — a render is not this head running our frames — but it does
> change what you are looking for, and it means a *negative* result is the
> interesting outcome rather than the boring one.

## ~~E1 — does byte 5 do anything?~~ SETTLED

**Answered 2026-09-10, incidentally.** With `source = gpu` in the config the
head rendered `GPU` in blue above the digits, where the manual's render of a
default head shows `CPU`. Byte 5 selects a text label. Nothing further to run.

<details><summary>original procedure, kept for reference</summary>

## E1 — does byte 5 do anything?

**Variable:** byte 5 only. Digits and level are pinned by `--test-value`, and
`--source` does not affect them in test mode.

```bash
montech-hyperflow --device /dev/montech-hyperflow --test-value 55 --source cpu --once
# 07 00 05 05 50 00

montech-hyperflow --device /dev/montech-hyperflow --test-value 55 --source gpu --once
# 07 00 05 05 50 01
```

The two frames differ in exactly one byte.

> **STOP — between those two, did *anything* on the head change?**
> Look specifically at the **text label above the digits**: the manual's
> render shows `CPU` there, so the expected change is `CPU` → `GPU`.

| Answer | Conclusion |
|---|---|
| nothing changed | Byte 5 is inert on this head. Promote item 1 to "no visible effect on the 240 model"; keep sending it for fidelity. |
| something changed | Describe it; that is a new documented field. |

---

</details>

## ~~E2 — what does `level` control?~~ SETTLED

**Answered 2026-09-10.** Two frames differing only in byte 4's high nibble,
digits pinned at `045`, photographed on the head: `level = 9` lit every
segment of the vertical bar (amber → orange → red bottom to top);
`level = 0` lit none at all. `level` is a discrete segment bar, N segments
for level N. It is not a colour or intensity ramp.

<details><summary>original procedure, kept for reference</summary>

## E2 — what does `level` control?

**Variable:** byte 4 high nibble only. `--test-level` overrides it while
`--test-value` pins the digits, so the digits are identical in all ten frames.

### E2a — endpoints first

```bash
montech-hyperflow --device /dev/montech-hyperflow --test-value 45 --test-level 0 --once
# 07 00 04 05 00 00
montech-hyperflow --device /dev/montech-hyperflow --test-value 45 --test-level 9 --once
# 07 00 04 05 90 00
```

> **STOP — the digits were `045` both times. Did anything else differ between
> level 0 and level 9?**
> Look at the **vertical bar on the left of the display**. The manual's render
> shows about ten segments running amber → orange → red bottom to top, and
> `level` has exactly ten values, so the expected difference is how many
> segments are lit: none (or one) at level 0, all of them at level 9.

If nothing differs at the extremes, the sweep is pointless — skip to `E3` and
record item 2 as "no visible effect".

### E2b — full sweep

Only if E2a showed a difference. Two seconds per step, digits pinned at `045`:

```bash
for L in 0 1 2 3 4 5 6 7 8 9; do
  echo "level $L"
  montech-hyperflow --device /dev/montech-hyperflow --test-value 45 --test-level $L --once
  sleep 2
done
```

> **STOP — describe the sequence. Was it a colour ramp (which colours, in what
> order), a brightness ramp, or something discrete?**

Record the exact mapping in `PROTOCOL.md`. If it is a colour ramp, this is the
most user-visible field in the protocol and belongs in the README.

> **Not run without your approval:** levels 10–15 are reachable in the nibble
> but the vendor clamps at 9. `--test-level` refuses them for that reason. If
> you want them probed, say so and I will widen the flag — it is byte 4, not
> byte 1, so it is not on the out-of-band command path.

---

</details>

## E3 — is the `0xFD` startup command required?

**Variable:** whether the init frame is sent, from a genuinely cold display.

The display must not have been initialised since power-on, or the answer is
meaningless. Two variants, weakest first:

### E3a — after a blank

```bash
montech-hyperflow --device /dev/montech-hyperflow --blank
montech-hyperflow --device /dev/montech-hyperflow --no-init --test-value 77
```

> **STOP — does the head show `077`?**

### E3b — after a real cold boot

The strong version. Power the machine fully off (not reboot — the pump head
keeps 5 V standby on many boards, so use the PSU switch or unplug if you can).
Boot, and **before running anything**:

> **STOP — what is the head showing right now, straight out of a cold boot?**

Then, without sending the init frame:

```bash
montech-hyperflow --device /dev/montech-hyperflow --no-init --test-value 77
```

> **STOP — does it show `077`?**

| Answer | Conclusion |
|---|---|
| `077` in both variants | `0xFD` is optional. Keep sending it (harmless, matches the vendor), but record it as not required. |
| works after blank, not after cold boot | `0xFD` is a genuine wake/init. Mandatory on open — which is what the driver already does on every reopen. |
| never works without init | Same conclusion, stronger. |

---

## E4 — does the *wrong* 65-byte length also work?

**Variable:** the on-wire frame length, 64 → 65.

Note the framing: 64 is now known-correct from the descriptor, so this asks
whether the oversized frame is *tolerated*, not which one is right.

**Risk:** a firmware that rejects the length may STALL the control endpoint.
A stalled endpoint is cleared by the kernel on the next transfer and is not
persistent, but if the firmware is unhappy it could also stop updating until
replugged. Low risk, easily recovered by a replug, but it is a real one.

```bash
montech-hyperflow --device /dev/montech-hyperflow --test-value 88 --frame-len 64 --once
montech-hyperflow --device /dev/montech-hyperflow --test-value 111 --frame-len 65 --once
```

Different values so you can tell which one landed.

> **STOP — did the head go `088` then `111`? Or did it stop at `088`? And what
> did the second command print on stderr?**

| Answer | Conclusion |
|---|---|
| both, no stderr | Firmware ignores the surplus byte. The vendor's oversized buffers would have been harmless even untruncated. |
| `088` only, `Broken pipe` on stderr | Firmware STALLs an oversized SET_REPORT — 64 is mandatory, and the old driver never worked. Strongest possible confirmation. |
| `088` only, no error | Firmware accepts and silently drops the transfer. |

Then confirm the head is still healthy:

```bash
montech-hyperflow --device /dev/montech-hyperflow --test-value 64 --once
```

> **STOP — back to `064`?** If not, replug the internal USB header.

---

## E5 — suspend/resume and monitor-off

> **Blocked on this machine as it stands.** `sleep.target`, `suspend.target`
> and `hibernate.target` are all **masked** here, so systemd cannot suspend at
> all and the `system-sleep` hook would never fire. `/sys/power/state` still
> lists `mem`, so the kernel can. Unmasking is your call:
> ```bash
> sudo systemctl unmask sleep.target suspend.target hibernate.target
> ```
> Until then E5a/E5b cannot run and the acceptance criterion "survives a
> suspend/resume cycle" cannot be demonstrated.

### E5a — suspend with nothing writing

```bash
montech-hyperflow --device /dev/montech-hyperflow --test-value 66 --once
systemctl suspend
```

Resume, and **before running anything**:

> **STOP — what is the head showing? `066`, blank, garbage, or something else?**

### E5b — suspend with the daemon running

```bash
montech-hyperflow --device /dev/montech-hyperflow --test-value 66
# leave running; suspend from another terminal or the desktop
```

Resume and watch the terminal.

> **STOP — did the head come back to `066` on its own, and did the terminal
> print a `reopened /dev/hidrawN` line?**

The daemon rediscovers the device and re-sends `0xFD` on every reopen, so this
should self-heal even if it re-enumerates under a new `hidrawN`.

### E5c — monitor off

No suspend involved; this only tests whether the head is on the same power
domain as the display output.

```bash
montech-hyperflow --device /dev/montech-hyperflow --test-value 66
```
Switch the monitor off at its own button, wait 30 s, switch it back on.

> **STOP — did the head keep showing `066` throughout?**

---

## E6 — the Output report path (optional)

The descriptor declares an **Output** instance of report 7, also 63 bytes,
which the vendor app never uses. If E4 shows the Feature path is fragile, this
is the fallback. The driver does not implement it yet — it is a ten-line
change, but there is no reason to run it unless the Feature path disappoints.

---

## Recording results

Every answer goes into `PROTOCOL.md` under **Verification status**, moving the
item out of the *Inferred* table. Update it in the same commit as any code
change the answer motivates, and say which claim the evidence contradicts.

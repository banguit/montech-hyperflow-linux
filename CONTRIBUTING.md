# Contributing

**This is an unofficial community driver.** It is not produced, endorsed or
supported by Montech, TCOMAS or SEMICO — see [`NOTICE.md`](NOTICE.md). Please
keep that framing in anything you write here, including package descriptions.

## The single most valuable contribution is a hardware report

Not code. The driver cannot see the pump head, so it cannot check its own
work. Several claims in [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — what the
`level` nibble does, whether byte 5 changes anything visible, whether the head
has a °C/°F indicator — are *inferred from a disassembly and nothing else*.
The only way to settle them is for a person with the cooler in front of them
to run one command and say what the display did.

[`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md) is that list of open questions,
written so each one takes about ten minutes. Pick one, run it, and file a
**Protocol finding** issue with what you saw.

### Negative results count. File them.

"I ran E2b through the whole 0–9 sweep and nothing on the head changed at all"
is a *result*. It is very likely the answer to E2, and it is the kind of
finding people quietly discard because it feels like they did something wrong.
They didn't — the experiment worked and the answer was no.

Equally worth filing:

- The head showed something, but not what the experiment predicted.
- The command errored before anything reached the device.
- Your cooler is a different brand and it worked unchanged. Say the brand; the
  same OEM pump head is sold under several, and owners of rebrands find this
  project by searching for their own name.
- Your cooler is a different brand and it did *not* work.
- Everything worked and there is nothing to report. That is data too.

Report an experiment result through the **Protocol finding** issue form, and
anything about whether the driver works on your machine through the
**Hardware report** form. Both ask for the same three diagnostics, all of
which write nothing to the device:

```bash
montech-hyperflow --list                              # devices, permissions, sensors
lsusb | grep -i 1a2c                                  # or the whole lsusb, if the ID differs
. /etc/os-release; echo "$PRETTY_NAME $(uname -r)"    # distro and kernel
```

### Experiment safety

The ground rules are at the top of `docs/EXPERIMENTS.md` and they exist
because this device has no authentication and no validation on the display
path. In short:

- **One variable per step**, and stop and look before changing the next thing.
- **Pin the device** with `--device /dev/montech-hyperflow`. `hidrawN`
  numbering moves across replug and resume.
- **Never write to interface 00.** That is the boot-keyboard interface. It has
  no display collection and nothing good can come of it.
- **Do not fuzz byte 1.** Values ≥ 2 there are out-of-band commands and only
  `0xFD` is identified. If you want to try something else, propose the exact
  bytes in an issue first.
- Every command has a `--dry-run` twin. Use it to see the frame before it goes
  out.

## Do not send vendor binaries or vendor code

**Do not attach, paste, link or commit any of the following:** the vendor's
installer or `DeviceDriver.exe`, any part of it, decompiler or disassembler
output, extracted firmware, vendor artwork, icons, fonts or strings.

Pull requests and issues containing them will be closed without merging. This
project documents the wire format needed to display a number, written from
scratch, and that is the basis on which it can exist at all. A repository
carrying vendor code is a different and much shorter conversation.

Citing an address — "the length check is at `0x1400132a0`" — is fine and is
what `docs/PROTOCOL.md` already does. Reproducing the code at that address is
not.

## Working on the code

No dependencies, no virtualenv, no build step:

```bash
make test    # the whole suite, hardware-free
make lint    # compiles everything and validates the udev/desktop/polkit files
```

Both must pass before you open a pull request. CI runs `make test` on Python
3.8 through 3.13, so keep to syntax that works on 3.8.

Three structural rules the CI enforces, because each one has a way of eroding
quietly:

1. **The daemon is standard library only.** `hid`, `frame`, `device`,
   `sensors`, `config`, `status`, `daemon`, `cli` and `admin` may import
   nothing outside the standard library. GTK and PyGObject live in
   `montech_hyperflow.tray` and are imported nowhere else, so a headless
   machine never installs them.
2. **The test suite is hardware-free and must stay that way.** No test may
   open a device node, and none may need a cooler, a network or a GPU. Fake
   the hidraw layer — `tests/test_hid.py` shows how, including a report
   descriptor crafted to fool a naive parser.
3. **The Makefile is the packaging contract.** Every package format
   (`deb`, `rpm`, `PKGBUILD`, Flatpak, AppImage) installs through
   `make install-core` / `make install-tray` with a `DESTDIR`. If you change
   where a file lands, update the assertions in
   `.github/workflows/ci.yml` in the same commit — that list is what catches
   a Makefile regression before it ships as a broken package.

Style: plain, obvious Python. The linting is deliberately limited to real
errors (undefined names, syntax mistakes) rather than a style bot, so match
the surrounding code rather than any particular formatter. Comments should
explain the non-obvious choice — why the udev file is called `72-`, why the
unit uses `Type=exec` — not restate the syntax.

## Protocol changes

If a change is motivated by something the hardware did, update
`docs/PROTOCOL.md` in the same commit: move the item between the confidence
tables (*Inferred* → *Confirmed on hardware*, or into *Corrected*), and say
which earlier claim the evidence contradicts. A protocol claim in this
repository is only worth what its stated evidence is worth.

## Pull requests

- One topic per pull request.
- Add a `## [Unreleased]` entry to [`CHANGELOG.md`](CHANGELOG.md) for anything
  a user would notice.
- Explain *why* in the commit message; the diff already says what.
- Do not bump `__version__` — releases are cut separately, and CI checks that
  the version, the tag and the newest changelog entry agree.

## Licensing

By contributing you agree that your contribution is licensed under
**GPL-3.0-or-later**, the license of this project. There is no CLA and no
copyright assignment: you keep your copyright.

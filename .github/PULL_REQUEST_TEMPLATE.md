<!--
Thanks for sending this.

If you are reporting what a pump head did rather than changing code, an issue
is the better home for it — the Protocol finding form asks for the fields that
make a result usable. Negative results are wanted.
-->

## What this changes, and why

<!-- The diff says what. Say why. -->

## How it was checked

- [ ] `make test` passes
- [ ] `make lint` passes
- [ ] Tried on real hardware — say which cooler and what the head did
- [ ] Not tried on hardware, and nothing here can affect what is sent to the
      device

<!-- If a claim about the protocol changed, quote what the head showed. -->

## Checklist

- [ ] The daemon modules import nothing outside the standard library; anything
      GTK is confined to `montech_hyperflow.tray`
- [ ] No test opens a device node — the suite stays hardware-free
- [ ] `docs/PROTOCOL.md` confidence tables updated, if this changes what is
      known about the wire format
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]`, if a user would
      notice this
- [ ] Installed paths unchanged, **or** the layout assertions in
      `.github/workflows/ci.yml` were updated to match
- [ ] Any new user-facing description still says this is an unofficial
      community driver, not a Montech product
- [ ] `__version__` not bumped (releases are cut separately)

## No vendor material

- [ ] This contains no vendor binaries, vendor code, decompiler output,
      firmware, artwork, icons, fonts or strings

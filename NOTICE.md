# Notice on names, trademarks and reverse engineering

## This is not Montech software

**montech-hyperflow is an unofficial, community-written Linux driver.** It is
not produced, endorsed, supported, reviewed or approved by Montech, by TCOMAS,
or by SEMICO. Do not contact any of them about it. If it breaks your cooler's
display, that is this project's fault, not theirs — and equally, this project
comes with no warranty of any kind (see `LICENSE`, sections 15 and 16).

"Montech" and "HyperFlow" are the marks of their respective owners. They are
used here only to say **which hardware this software talks to**, which is the
only way to say it accurately. No claim of ownership or affiliation is made or
implied.

## What was done, and why that is normal

The display protocol was recovered by disassembling the vendor's own Windows
application, which the vendor distributes freely, in order to make the
hardware work with a different operating system. That is interoperability
reverse engineering — the long-standing and widely relied-upon basis for
Linux drivers for consumer hardware, from printers to game controllers to
every other AIO cooler with an RGB header.

This project:

- ships **no** vendor code, binaries, firmware, images, fonts or strings;
- ships **no** Montech logo or artwork — the icons here are original work;
- does not modify, patch, redistribute or circumvent the vendor application,
  and does not require you to have it;
- documents only the wire format needed to display a number, in
  `docs/PROTOCOL.md`, with the addresses cited so anyone can verify the claims
  independently.

Nothing here decrypts, unlocks or bypasses anything. The device has no
authentication, no signing and no access control on the display: it accepts an
unauthenticated HID feature report from anyone who can open the device node.

## If you are Montech, TCOMAS or SEMICO

You are welcome here. Two things would be genuinely useful, and both would let
this project delete a lot of guesswork:

1. Confirmation or correction of `docs/PROTOCOL.md` — particularly what the
   `level` nibble and byte 5 actually drive on the pump head.
2. Whether you would prefer different wording anywhere in this repository.

Open an issue. If there is a specific, concrete concern about naming or
presentation, the maintainers intend to address it rather than argue about it.

*This file is a statement of intent and practice, not legal advice.*

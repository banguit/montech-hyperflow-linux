# Arch Linux packaging

Builds two packages from one `PKGBUILD`:

| Package | Contents |
| --- | --- |
| `montech-hyperflow` | daemon, CLI, admin helper, udev rule, systemd unit, sleep hook, docs |
| `montech-hyperflow-tray` | panel indicator, icons, `.desktop` entry, polkit action |

They are split so a headless machine never pulls GTK. The tray package
contains no Python of its own — it imports `montech_hyperflow` from the core
package's private libdir, which is why it depends on an exact
`montech-hyperflow=$pkgver-$pkgrel`.

> **This is an unofficial community driver.** It is not affiliated with,
> authorised by, or endorsed by Montech. It targets the pump-head display that
> enumerates as USB `1a2c:4e85` (OEM platform TCOMAS DH-C100), so rebrands of
> the same hardware work too. No vendor artwork is shipped.

## Build and install

From this directory, on an Arch system:

```sh
cd packaging/arch
makepkg -si            # build both packages and install them
```

Or build without installing, then install by hand:

```sh
makepkg -f
sudo pacman -U montech-hyperflow-1.0.0-1-any.pkg.tar.zst
sudo pacman -U montech-hyperflow-tray-1.0.0-1-any.pkg.tar.zst   # optional
```

Core-only, on a headless box: build both (they come from one `PKGBUILD`) and
install just the first. `makepkg` cannot build one half of a split package.

Requirements: `base-devel` (for `makepkg` and `make`) and `python`. There is
no compile step, no build-time network access, and the result is
`arch=('any')`.

### Skipping the test suite

`check()` runs the hardware-free suite. It reads `$XDG_RUNTIME_DIR`, and four
status tests fail if a live `montech-hyperflow` status file happens to be
sitting there — which is the case when you build on a machine that is
currently running the daemon. Build in a clean chroot (`extra-x86_64-build`),
or skip the tests:

```sh
makepkg -si --nocheck
```

## Where the source comes from

`source=()` is empty. The `PKGBUILD` builds the working tree two directories
up — the repository it ships inside — including uncommitted edits. That is
deliberate while the project has no chosen owner and no release tag: it means
no network, no tarball to keep in sync, and `makepkg` builds exactly what you
are looking at.

Build a checkout somewhere else with:

```sh
_srctree=/path/to/montech-hyperflow makepkg -si
```

At release time, once `OWNER` is real and a `v1.0.0` tag exists, swap in a
proper source array — the header comment in the `PKGBUILD` spells out the
three-line change. `prepare()` refuses to build if `pkgver` and
`__version__` in `src/montech_hyperflow/__init__.py` disagree, so a forgotten
version bump fails the build instead of shipping a mislabelled package.

## Arch-specific decisions

**The udev rule goes to `/usr/lib/udev/rules.d`, not `/etc`.** The `Makefile`
defaults `UDEVDIR` to `/etc/udev/rules.d`, which is right for Debian; the
`PKGBUILD` overrides it via the `_udevdir` variable. Arch reserves
`/etc/udev/rules.d` for the administrator, so keeping the packaged rule in the
vendor directory means pacman never has to arbitrate a `.pacnew` over a file
someone edited, and a same-named file dropped into `/etc` still overrides it.
The `72-` prefix keeps working: udev sorts the merged rule set by filename
across both directories, so the rule is still evaluated before
`73-seat-late.rules`, which is what makes its `TAG+="uaccess"` take effect.
Set `_udevdir=/etc/udev/rules.d` to match the Debian layout instead.

**The systemd unit's `ExecStart=` is rewritten in `prepare()`.** The unit in
`packaging/systemd/` hardcodes `/usr/local/bin/montech-hyperflow`, the
`make install` default. A distro package installs with `PREFIX=/usr`, so that
path does not exist and the unit fails at every start. `prepare()` rewrites it
to `/usr/bin/` and aborts the build if the pattern ever stops matching.

**`/etc/montech-hyperflow.conf` is not in `backup=()`.** The package does not
ship that file — only the annotated example, in
`/usr/share/doc/montech-hyperflow/`. pacman only tracks backup paths for files
a package actually owns, so a config you create there is never touched on
upgrade or removal. That is the behaviour we want; adding a `backup=` entry
for a file we do not install would do nothing.

**No `install=` scriptlet for the tray package.** The GTK icon cache, the
desktop database and polkit's action directory are all refreshed by pacman
hooks shipped with `gtk-update-icon-cache`, `desktop-file-utils` and `polkit`.

## The `.install` scriptlet

`montech-hyperflow.install` exists for two things pacman will not do:

1. **Create the `plugdev` group.** The systemd unit runs with
   `DynamicUser=yes` and `SupplementaryGroups=plugdev`, and the udev rule sets
   `GROUP="plugdev"`. Debian ships that group; **Arch does not create it at
   all.** Without it the unit refuses to start (`Failed to determine
   supplementary groups`) and the hidraw node stays `root:root`. The scriptlet
   creates it as a system group if it is missing, before touching udev — udev
   resolves `GROUP=` at rule-apply time, so the order matters.

   It is never removed on uninstall: `plugdev` is a shared, conventional group
   name that other packages may be relying on.

   An alternative would be a `sysusers.d` file, which is the more idiomatic
   Arch mechanism for declaring users and groups. It is not used here because
   `plugdev` is not this package's group to own — the scriptlet's
   "create only if absent" is the more honest statement.

2. **Re-apply the rule to an already-connected cooler.** A `udevadm control
   --reload` only affects devices that appear afterwards, so a cooler that was
   plugged in at install time keeps its old permissions until it is re-added.
   The scriptlet follows the reload with
   `udevadm trigger --subsystem-match=hidraw --action=add`, which is the
   difference between "works now" and "works after you reboot". Both calls are
   guarded, so installing inside a container or chroot does not raise an error.

## After installing

The service is **not** enabled. Driving the pump head is opt-in:

```sh
montech-hyperflow --list                       # is the device visible?
sudo systemctl enable --now montech-hyperflow.service
systemctl status montech-hyperflow.service
```

A logged-in desktop user gets access automatically — the udev rule tags the
device `uaccess`. A headless or non-seat account needs the group by hand, and
a fresh login afterwards:

```sh
sudo usermod -aG plugdev "$USER"
```

To change the temperature source or units:

```sh
sudo cp /usr/share/doc/montech-hyperflow/montech-hyperflow.conf.example \
        /etc/montech-hyperflow.conf
sudoedit /etc/montech-hyperflow.conf
sudo systemctl restart montech-hyperflow.service
```

For the indicator, install `montech-hyperflow-tray` and run
`montech-hyperflow-tray`, or launch "Montech HyperFlow" from your application
menu. To start it with the session, copy
`packaging/desktop/montech-hyperflow-tray-autostart.desktop` into
`~/.config/autostart/`.

## Notes for the packager

`namcap` will warn that both `pkgdesc` strings exceed the 80-character
guideline. That is expected. **Do not fix it by trimming the "unofficial / not
affiliated with Montech" wording** — the disclaimer is a hard requirement.
`namcap` may also flag the `python-gobject` dependency as unused, because the
tray imports it through `gi` at runtime rather than at module scope.

`optdepends` lists `nvidia-utils` because `--source gpu` falls back to
`nvidia-smi` for cards that expose no hwmon temperature. If you use that on an
NVIDIA box, the unit also needs its commented-out `DeviceAllow=/dev/nvidia*`
lines uncommented — `DevicePolicy=closed` blocks them otherwise.

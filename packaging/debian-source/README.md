# Debian source package (debhelper)

This directory is a `debian/` directory. It is **not** named `debian/` and it
does not live at the repository root, because there is a second, unrelated set
of Debian files in `packaging/debian/`, and the two must not be confused.

## Why there are two debian directories

| | `packaging/debian/` | `packaging/debian-source/` (here) |
|---|---|---|
| Built by | `make deb` | `dpkg-buildpackage` |
| Needs | `dpkg-deb` only | `debhelper`, `dpkg-dev`, `build-essential` |
| Produces | **one** `.deb` with the daemon *and* the tray | **two** `.deb`s: `montech-hyperflow` and `montech-hyperflow-tray` |
| Contents | `control.in`, `postinst`, `postrm`, `conffiles` — the four files `dpkg-deb --build` reads | a full source package: `control`, `rules`, `changelog`, `copyright`, `*.install` |
| GUI libraries | `Recommends` | `Depends` of the separate `-tray` package |
| udev rule | `/etc/udev/rules.d/`, a conffile | `/usr/lib/udev/rules.d/`, not a conffile |
| For | local builds, CI artifacts, "give me a .deb now" | uploading to a repository, an Ubuntu PPA, or a distribution |

`make deb` stages the whole install tree with the Makefile and then hands
`dpkg-deb` a hand-written control file. That is fast, has almost no build
dependencies, and is the right tool for a release artifact attached to a
GitHub tag. It cannot split a package, cannot generate maintainer-script
fragments, and cannot produce a source package — so it is not the right tool
for a repository upload. Hence this directory.

Both consume exactly the same `make install-core install-tray` staging step,
so the two never drift on *what* gets installed, only on *how* it is split
and where a couple of files land.

## Building

The source format is `3.0 (quilt)`, so the build wants an upstream tarball
next to the source tree. From a clean checkout:

```sh
make tarball                                   # montech-hyperflow-1.0.0.tar.gz
mv montech-hyperflow-1.0.0.tar.gz ../montech-hyperflow_1.0.0.orig.tar.gz
cp -r packaging/debian-source debian
dpkg-buildpackage -us -uc                      # add -b for a binary-only build
rm -rf debian                                  # it is a copy; do not commit it
```

The two `.deb`s and the `.dsc` land in the parent directory. Check them with:

```sh
lintian ../montech-hyperflow_1.0.0-1_amd64.changes
dpkg-deb -c ../montech-hyperflow_1.0.0-1_all.deb
dpkg-deb -c ../montech-hyperflow-tray_1.0.0-1_all.deb
```

`DEB_BUILD_OPTIONS=nocheck` skips the test suite, which `dh_auto_test`
otherwise runs through the Makefile's `test` target.

To build native instead (no orig tarball, version `1.0.0` with no `-1`),
change `source/format` to `3.0 (native)` and drop the Debian revision from
`changelog`.

## Decisions worth knowing about

**There is no `debian/compat`.** The compatibility level is declared once, as
`debhelper-compat (= 13)` in `Build-Depends`. Shipping both is an error —
debhelper refuses to run with the level specified in two places — and the
`compat` file is the deprecated half of that pair.

**No `dh-python`, no `pybuild`, no `python3-setuptools`.** The package
installs into a private libdir, `/usr/lib/montech-hyperflow`, with generated
`/usr/bin` wrappers, rather than into `site-packages`. That is deliberate
upstream: it removes any coupling to the distribution's Python version, and it
means a Python upgrade cannot break the driver. The cost is that none of the
Python packaging helpers apply, and the gain is that none of them are needed —
`Depends: python3 (>= 3.8)` is the whole story.

**The udev rule moves to `/usr/lib/udev/rules.d`.** The Makefile defaults
`UDEVDIR` to `/etc/udev/rules.d`, which is right for `sudo make install` but
wrong for a Debian package: `/etc` belongs to the administrator, and a
package's own rules belong in `/usr/lib`, where an admin overrides them by
dropping a same-named file into `/etc`. `debian/rules` passes `UDEVDIR`
explicitly. Ordering is unaffected — udev merges all its rules directories and
sorts by file name, so `72-` still runs before `73-seat-late.rules`, which is
the constraint that makes `TAG+="uaccess"` actually take effect.

**`/etc/montech-hyperflow.conf` is not a conffile in either build**, because
neither build ships it. It is optional, it is created by hand or by the
`pkexec` helper, and the example lives in `/usr/share/doc`. `postrm purge`
removes it. Listing an unshipped path in `DEBIAN/conffiles` is not merely
untidy: `dpkg-deb --build` rejects it outright with *"conffile ... does not
appear in package"*.

**`debian/rules` patches `ExecStart` in the systemd unit.** See the comment
in `override_dh_auto_install`. `packaging/systemd/montech-hyperflow.service.in`
hardcodes `/usr/local/bin/montech-hyperflow`, which is correct for the
Makefile's default `PREFIX=/usr/local` and wrong for every distribution
package, where the wrapper is installed to `/usr/bin`. The unit is
`Type=exec`, so the service would fail to start with `203/EXEC`. **This is an
upstream bug and it affects every packaging format equally** — the fix belongs
in the unit file (or in a Makefile substitution like the one already used for
the wrappers), after which the `sed` here should be deleted. The `grep`
following it fails the build if the substitution ever stops matching.

**The service is shipped disabled** (`dh_installsystemd --no-enable
--no-start`). Whether the pump head is driven at boot is the administrator's
decision, and the package description asks them to confirm
`montech-hyperflow --list` finds the device first.

## Placeholders to substitute at release time

- `OWNER` in `control`, `copyright` and `changelog` — the GitHub owner, not
  yet chosen. Same placeholder as the rest of the repository.
- The `Maintainer:` address, currently a `users.noreply.github.com` form.
- The version in `changelog`. It is **not** generated: `make deb` substitutes
  `@VERSION@` into `packaging/debian/control.in` from
  `src/montech_hyperflow/__init__.py`, but a Debian source package takes its
  version from the changelog and nowhere else. Bump it with
  `dch -v <version>-1` as part of the release, and keep it in step with
  `__version__`.

## A note on comments

Comments are legal in this directory (`control`, `rules`, the `*.install`
files, `not-installed` — dpkg-dev and debhelper all strip `#` lines) but they
are **not** legal in the binary control file that `packaging/debian/control.in`
becomes: `dpkg-deb` fails with *"field name '#' must be followed by colon"*,
and the Makefile copies that file through `sed` verbatim. That is why the
rationale for `Recommends` over `Depends` in the single-`.deb` build lives in
its extended `Description` instead, where users of `apt show` will also read
it.

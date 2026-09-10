# AppImage packaging

> **Unofficial community driver.** Not produced, endorsed or supported by
> Montech. Hardware: USB **`1a2c:4e85`**, OEM platform **TCOMAS DH-C100**.
> No vendor code or artwork is included. See [`NOTICE.md`](../../NOTICE.md).

One file that carries the tray indicator **and** the CLI/daemon. It cannot
install the udev rule or the system service by itself — an AppImage never
touches the host — so there is a small root script inside it,
`montech-hyperflow-setup`, that installs the one thing the driver genuinely
cannot work without.

## What is and is not in the bundle

**In:** the `montech_hyperflow` package, the hicolor icons, the `.desktop`
file, the docs (README, NOTICE, LICENSE, PROTOCOL, EXPERIMENTS, the example
config), the udev rule as *data* for the setup script, and `AppRun`.

**Not in: a Python interpreter, GTK, or PyGObject.** The driver is pure
standard library, so it runs on whatever `python3` the host already has
(3.8 or newer — set `MONTECH_PYTHON` to point at a different one). The tray
additionally needs the host's PyGObject, GTK 3 and the
`AyatanaAppIndicator3-0.1` typelib:

| Distro | Packages |
| --- | --- |
| Debian/Ubuntu | `python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1` |
| Fedora | `python3-gobject gtk3 libayatana-appindicator-gtk3` |
| Arch | `python-gobject gtk3 libayatana-appindicator` |

This is a deliberate trade-off: bundling a GTK stack would multiply the size
by two orders of magnitude to ship a driver whose entire payload is a few
dozen kilobytes of Python. If the CLI is all you want, nothing above is
needed.

## Build

```bash
packaging/appimage/build-appimage.sh [OUTDIR]     # OUTDIR defaults to build/
```

The AppDir is filled in through the Makefile's `DESTDIR`/`PREFIX` contract —
the same one the `.deb`, the RPM and the snap use — so what lands in the
bundle is exactly what a distro package would install, minus the pieces an
AppImage may not install. Targets used: `install-lib install-icons
install-desktop install-doc install-udev`. Deliberately **not** used:

* `install-unit` — an AppImage cannot install a systemd unit or a sleep hook;
* `install-polkit` — nor a polkit action;
* `install-bin` — the generated `/usr/bin` wrappers hardcode an absolute
  `/usr/lib/montech-hyperflow` and put it first on `sys.path`, so on a machine
  that *also* has the distro package installed they would silently run that
  copy instead of the bundled one. `AppRun` launches the package out of the
  AppDir directly.

`appimagetool` is **not** downloaded by the build script — a package build
that fetches an unpinned binary over the network is not something to hide in a
build step. Install it yourself (from
<https://github.com/AppImage/appimagetool/releases>, or your distro's
`appimagetool`/AppImageKit package) or point `APPIMAGETOOL` at it. If it is
missing, the AppDir is still built and the script prints the exact command to
finish the job, then exits non-zero.

The payload is architecture-independent, but the AppImage *runtime* stub that
`appimagetool` prepends is not, so the filename carries `$ARCH`
(`montech-hyperflow-0.3.0-x86_64.AppImage`). Set `ARCH` to cross-name it.

## Running it

`AppRun` picks a mode from, in order: an explicit first argument, the name it
was invoked as (`ARGV0`, so symlinks work), then whether there are arguments
at all.

```bash
./montech-hyperflow-0.3.0-x86_64.AppImage                 # tray (also what a
                                                          # double-click does)
./montech-hyperflow-0.3.0-x86_64.AppImage tray            # the same, explicit
./montech-hyperflow-0.3.0-x86_64.AppImage --list          # CLI: any argument
./montech-hyperflow-0.3.0-x86_64.AppImage cli --status    # CLI, explicit
./montech-hyperflow-0.3.0-x86_64.AppImage help            # the mode list
sudo ./montech-hyperflow-0.3.0-x86_64.AppImage setup      # one-time host setup

ln -s montech-hyperflow-0.3.0-x86_64.AppImage montech-hyperflow-tray
ln -s montech-hyperflow-0.3.0-x86_64.AppImage montech-hyperflow
./montech-hyperflow-tray        # name decides: tray
./montech-hyperflow --once      # name decides: CLI
```

`AppRun` also puts the AppDir's `usr/share` at the front of `XDG_DATA_DIRS`
(so GTK finds the bundled icons) and *appends* its `usr/bin` to `PATH`. The
append is deliberate: the tray resolves `montech-hyperflow-admin` by name and
hands it to `pkexec`, which must get a root-owned copy from a real system
install, never one inside a squashfs mounted by the calling user.

## One-time host setup

```bash
sudo ./montech-hyperflow-0.3.0-x86_64.AppImage setup
```

The pump head is a hidraw device. With no udev rule its `/dev/hidrawN` node is
`0600 root:root` and nothing but root can open it. The setup script:

1. creates the `plugdev` **group** if the distro does not have one (Debian and
   Ubuntu do; Fedora and Arch frequently do not) — before the udev trigger, so
   the name in the rule resolves when the event fires;
2. installs `/etc/udev/rules.d/72-montech-hyperflow.rules`, read out of the
   AppDir rather than embedded here so it can never drift from
   `packaging/udev/`;
3. runs `udevadm control --reload` and
   `udevadm trigger --subsystem-match=hidraw --action=add`, because a reload
   alone only affects devices that appear afterwards;
4. adds the invoking user (from `SUDO_USER`/`PKEXEC_UID`, or a username you
   pass) to `plugdev`.

It refuses to run as anyone but root, every step checks before acting so
re-running it changes nothing, it prints exactly what it changed, and it ends
with the commands to undo all of it. **It installs no service and enables
nothing.**

## What an AppImage cannot do

| | |
| --- | --- |
| the udev rule | only via `sudo <appimage> setup`, above. Nothing else in the bundle touches the host. |
| the system service | **not installed.** No `montech-hyperflow.service`, so the tray's service controls report "not installed" and its start/stop items do nothing useful — they drive `systemctl` against a *system* unit that is not there. Drive the display from a user unit instead (below). |
| the suspend hook | **not installed.** The head is not blanked before suspend, so it keeps showing the last temperature while the machine sleeps. It heals on resume: the daemon re-detects the device and re-sends the init frame. |
| the polkit action | **not installed**, so the tray's *Apply settings* (which runs `pkexec montech-hyperflow-admin`) fails unless the real package is also installed. Edit `~/.config/montech-hyperflow.conf` by hand instead — it takes precedence over `/etc/montech-hyperflow.conf` and needs no privileges at all. |
| desktop integration | **not automatic.** The `.desktop` file and icons are inside the bundle; nothing puts them in your menu. Use an AppImage integrator, or copy `montech-hyperflow-tray.desktop` into `~/.local/share/applications/` yourself and fix its `Exec=` to the AppImage's absolute path. |

## The working recipe

```bash
sudo ./montech-hyperflow-0.3.0-x86_64.AppImage setup     # once
# log out and back in, so the plugdev membership applies
./montech-hyperflow-0.3.0-x86_64.AppImage --list         # should say "writable"
./montech-hyperflow-0.3.0-x86_64.AppImage --source cpu --blank-on-exit &
./montech-hyperflow-0.3.0-x86_64.AppImage tray
```

The tray does find that daemon, and this is worth understanding: the daemon
publishes its status to `/run/montech-hyperflow/status.json`, cannot create
that directory as an ordinary user, and falls back to
`$XDG_RUNTIME_DIR/montech-hyperflow/status.json`. The tray, running as the
same user, looks in the same two places in the same order. So a *user*-run
daemon and a *user*-run tray meet in `/run/user/$UID/`, with no root and no
service involved.

To keep it running, write your own user unit — the AppImage will not install
one for you:

```ini
# ~/.config/systemd/user/montech-hyperflow.service
[Unit]
Description=Montech HyperFlow pump-head display (AppImage)

[Service]
Type=exec
ExecStart=%h/Applications/montech-hyperflow-0.3.0-x86_64.AppImage cli --source cpu --blank-on-exit
Restart=on-failure
RestartSec=5
# Exit 78 (EX_CONFIG) is a permanent misconfiguration; restarting cannot fix it.
RestartPreventExitStatus=78

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now montech-hyperflow
```

A user unit runs only while you are logged in; `loginctl enable-linger $USER`
keeps it alive from boot to shutdown if that is what you want. Either way the
tray's own service menu still reads "not installed", because it asks about the
*system* unit.

If you want the service, the sleep hook, the polkit helper and the display
running before anyone logs in, install the distro package or run
`sudo make install` from the source tree. That is what those exist for.

## Verification status

`appimagetool` is not installed in the environment where this was written, so
**no .AppImage file has been produced from these scripts**. What was actually
run:

* `sh -n` and `dash -n` on `AppRun`, `build-appimage.sh` and
  `montech-hyperflow-setup`; all three are mode 0755;
* `build-appimage.sh` end to end against this tree: it staged a complete
  AppDir (`AppRun`, `montech-hyperflow-tray.desktop`, `.DirIcon`,
  `montech-hyperflow.svg`, `usr/lib/montech-hyperflow/montech_hyperflow/**`,
  `usr/share/{applications,icons,doc,metainfo}`, `etc/udev/rules.d/`,
  `usr/bin/montech-hyperflow-setup`), passed its own completeness check, then
  stopped at the missing `appimagetool` with the message above and exit 1;
* `AppRun` dispatch out of that AppDir, on real hardware: `--version`,
  `cli --version`, `help`, `--list` (found the device and the hwmon sensor),
  `--dry-run --once` (rendered a frame), `tray` and the two symlink names
  (`montech-hyperflow-tray` → tray, `montech-hyperflow` → CLI), with and
  without `$APPDIR` set;
* `montech-hyperflow-setup` refusing to run as a non-root user; and its full
  body with `id`/`install`/`udevadm`/`getent`/`groupadd`/`gpasswd` replaced by
  stubs, covering: rule already current, rule changed, `plugdev` present,
  `plugdev` missing (created), user already a member, user added, no user
  determinable, and an invalid username.

Not checked, because it needs `appimagetool` or a second machine: the packed
`.AppImage` itself, whether `appimagetool`'s desktop-file validation is happy
with the bundled entry, and the behaviour of `$ARGV0`/`$APPIMAGE` as set by
the real runtime (they were supplied by hand in the tests above).

# Flatpak

**This Flatpak is the tray indicator only. On its own it does nothing.**

A Flatpak cannot install a udev rule and cannot install a systemd system
unit. Those two files are what give the driver access to `/dev/hidrawN` and
what make it run at boot, before and after anyone logs in. Nothing in the
sandbox model bends far enough to replace them, and no combination of
`finish-args` changes that.

So the split is:

| Piece | Where it has to come from |
|---|---|
| the daemon that drives the pump head | native package (`.deb`, `.rpm`, Arch) |
| udev rule `72-montech-hyperflow.rules` | native package |
| `montech-hyperflow.service` + sleep hook | native package |
| polkit action + `montech-hyperflow-admin` | native package |
| **the panel indicator** | **this Flatpak** |

Install the native `montech-hyperflow` package first. Without it this Flatpak
is a panel icon that permanently reads *"Daemon not running"*, and every menu
item that does something will fail.

If you are choosing a package format and you want the thing to work, install
the native package for your distro and take its tray subpackage too. This
Flatpak exists for people who have a reason to want the GUI half sandboxed
and updated separately — not as the recommended path.

---

## What the tray actually needs, and what that costs

The tray is a client. It never opens the hidraw device; the daemon owns it,
because two writers to one display fight at 1 Hz. It does exactly three
things, and all three of them reach outside a sandbox:

1. **Reads `/run/montech-hyperflow/status.json`** — the daemon rewrites it
   atomically every tick.
2. **Runs `systemctl`** to start/stop/query `montech-hyperflow.service`.
3. **Runs `pkexec montech-hyperflow-admin set …`** to persist a setting to
   `/etc/montech-hyperflow.conf`.

### 1. The status file — genuinely cheap

```
--filesystem=/run/montech-hyperflow:ro
```

Read-only, one directory, and the tray never writes there. This one is fine
and needs no apology. Flatpak bind-mounts the host directory at the same path
inside the sandbox, so `status.py`'s hardcoded `/run/montech-hyperflow/
status.json` resolves without a code change.

### 2 and 3. systemctl and pkexec — the leak

There is no honest way to make these work from inside a sandbox as the tray
is currently written. Here is what was considered and why each option was
rejected or accepted.

**Rejected: `--system-talk-name=org.freedesktop.systemd1`.**
This would genuinely work *for the systemd half*. `StartUnit` / `StopUnit` /
`GetUnit` on `org.freedesktop.systemd1.Manager` are ordinary system-bus
methods, and polkit prompts through the session's auth agent exactly as it
does for `systemctl`. But `tray/service.py` calls the `systemctl` **binary**
as a subprocess, and that binary is not in the runtime. Using the bus instead
means writing a D-Bus client — and the whole point of this project's design
is that neither half depends on a D-Bus binding. It is a real option, but it
is a code change, not a manifest change.

**Rejected: `--system-talk-name=org.freedesktop.PolicyKit1`.**
This is a trap and worth spelling out, because it looks like the answer. The
polkit D-Bus API lets you *ask whether* a subject is authorised for an action.
It does not run anything as root. `pkexec` is a setuid binary on the host;
there is no bus method meaning "now execute this program with privilege". So
this permission buys nothing at all here. Granting it would be security
theatre.

**Accepted, with both eyes open: `--talk-name=org.freedesktop.Flatpak`.**
That is the Development portal, the thing behind `flatpak-spawn --host`. The
build drops three shims into `/app/bin` — `systemctl`, `pkexec` and
`montech-hyperflow` — that forward to the host binaries of the same name.
`shutil.which()` finds them, so the unchanged tray code works.

**Be clear about what this is: a full sandbox escape.** It grants arbitrary
command execution on the host, as the invoking user, outside any confinement.
Anything the user can run, this Flatpak can run. It is strictly more
permissive than `--filesystem=home`, `--device=all` and `--share=network` put
together.

### The session bus is the same hole again

```
--socket=session-bus
```

`libayatana-appindicator` owns a bus name of the form
`org.kde.StatusNotifierItem-<pid>-<n>`. Flatpak's `--own-name` understands
exact names and `.*` subtrees, and a PID-derived name is neither, so it cannot
be declared ahead of time. The tidy-looking alternative —

```
--talk-name=org.kde.StatusNotifierWatcher
--talk-name=org.ayatana.indicator.application
```

— lets the tray *find* the watcher but not *register* with it, and the icon
silently never appears on most shells.

And unfiltered session-bus access already includes `org.freedesktop.Flatpak`,
so this line is a sandbox escape on its own, independently of the one above.
Removing the `--talk-name=org.freedesktop.Flatpak` line while keeping
`--socket=session-bus` would confine nothing; it would only make the manifest
read as though it did.

### Verdict

**The sandboxing on this Flatpak is cosmetic. Do not treat it as a security
boundary.** It gets you Flatpak's packaging, delta updates and runtime
management. It does not get you isolation. That is a fair trade only if you
know you are making it, which is why it is written down here rather than
buried in a comment.

What is *not* granted, and is not needed:

- `--device=all` — the tray never touches `/dev/hidraw*`. (The top-level
  `README.md` package table claims the Flatpak needs `--device=all`. That is
  wrong; this manifest does not use it.)
- `--share=network` — nothing here talks to a network, ever.
- `--filesystem=home` — no user files are read or written.

### The restricted variant

Delete the `--talk-name=org.freedesktop.Flatpak` line **and** swap
`--socket=session-bus` for the two `--talk-name` lines above, and you get a
build that is meaningfully confined. What still works:

- the panel icon and its label
- the live temperature, unit, source and sensor readout

What breaks, in this exact way — `service._run()` catches `FileNotFoundError`
and returns `(False, "<cmd> not found")`, so nothing crashes:

- *Start/Stop display service* → error dialog, `systemctl not found`
- *Celsius/Fahrenheit* and *Show CPU/GPU* → error dialog, `pkexec not found`
- *Blank the display now* → error dialog
- `unit_state()` returns `"unknown"`, so the menu header reads
  *"Daemon not running"* even while the service is running fine

and the indicator may not register with the shell at all, per the bus-name
problem above.

### The fix that would make this honest

Give the daemon a small D-Bus system service with `SetConfig`, `Start` and
`Stop` methods, each polkit-checked on its own action id. Then the Flatpak
needs one `--system-talk-name=org.montech.HyperFlow1` and nothing else: no
host command execution, no unfiltered session bus, no shims. That is a real
design change to the daemon — it would be the first D-Bus dependency in a
codebase that deliberately has none — and it is out of scope for packaging.

---

## Building

```bash
cd packaging/flatpak
flatpak install -y flathub org.gnome.Platform//48 org.gnome.Sdk//48
flatpak-builder --force-clean --user --install build org.montech.HyperFlow.yaml
flatpak run org.montech.HyperFlow
```

The manifest's app module uses a `type: dir` source pointing at the repo
root, so it builds from your working tree. Swap in the commented-out `git`
source for a release build.

The Flatpak build reuses the same `make install-tray` the `.deb` uses, with
`PREFIX=/app`. That target pulls in `install-lib`, `install-icons`,
`install-desktop` and `install-polkit` and deliberately does **not** pull in
`install-udev`, `install-unit` or `reload-udev` — which is the only reason a
sandboxed build can share a Makefile with a system package at all. The build
then deletes `/app/share/polkit-1`: host polkit reads
`/usr/share/polkit-1/actions` and cannot see inside a Flatpak, so a copy in
there authorises nothing and only misleads whoever audits the bundle.

### The AppIndicator modules will need attention

`org.gnome.Platform` ships GTK 3 and PyGObject but no indicator typelib at
all, so the manifest builds the whole Ayatana chain: `libdbusmenu` →
`libayatana-ido` → `libayatana-indicator` → `libayatana-appindicator`.

**Those four modules were written from knowledge, without network access. The
repository URLs, tags and buildsystems have not been fetched, and no build of
them has been run.** Verify each before you trust the manifest. `libdbusmenu`
is the fragile one — old autotools, wants `intltool`, and its gtk-doc and Vala
paths break more often than they build.

A shortcut worth knowing: `tray/app.py`'s `_load_appindicator()` tries
`AyatanaAppIndicatorGLib` (1), then `AyatanaAppIndicator3` (0.1), then
`AppIndicator3` (0.1). That last fallback means
[flathub/shared-modules](https://github.com/flathub/shared-modules)'
`libappindicator/libappindicator-gtk3-12.10.json` is a valid substitute for
all four modules, and it is a far better-trodden path. It provides the old
`AppIndicator3-0.1` typelib, which the loader accepts. Vendor shared-modules
as a git submodule and replace the four modules with one `- shared-modules/…`
include.

---

## Known limitations inside the Flatpak

**"Protocol notes" always reports the docs are missing.** `app.py` looks in
`/usr/share/doc/montech-hyperflow` and `/usr/local/share/doc/…`. Inside a
Flatpak, `/usr` is the runtime, not the app — the docs are at
`/app/share/doc/montech-hyperflow` (the build does install them there). The
one-line fix is to add `"/app/share/doc/montech-hyperflow"` to `DOC_DIRS`;
even then, handing a `file:///app/...` URI to the host's browser via the
OpenURI portal will not work without exporting it through the document
portal, so this menu item wants a proper look rather than a quick patch.

**No autostart.** The native package ships
`montech-hyperflow-tray-autostart.desktop`. A Flatpak cannot drop a file into
the host's `~/.config/autostart` without the Background portal, and the app
never requests it. Add it by hand in your desktop's Startup Applications; it
will run `flatpak run org.montech.HyperFlow`.

**Icons are installed under two names, on purpose.** Flatpak only exports
icons whose basename starts with the app ID, and the exported copy is what the
host sees — but `app.py` asks the icon theme for `montech-hyperflow` and falls
back to a generic thermometer if it is missing, so the in-sandbox name has to
survive too. The build keeps both.

The panel icon itself is resolved by the *host* shell, not by us: a status
notifier item carries an icon *name* across the bus, and the host looks it up
in its own hicolor theme. In the supported configuration the native package
has already put `montech-hyperflow.svg` there, so the right icon appears. On a
host without the native package you get a generic thermometer — which is
consistent with the fact that nothing else works there either.

**`pkexec` path matching.** `service.apply_settings()` resolves the helper to
`/usr/bin/montech-hyperflow-admin` (the `which()` lookup misses inside the
sandbox, so the hardcoded fallback wins), and host polkit matches that against
the `exec.path` annotation in the shipped action. That lines up when the
native package was installed with `PREFIX=/usr`, which is what every distro
package does. A `sudo make install` with the default `PREFIX=/usr/local` puts
the helper somewhere the shipped polkit action does not name — a pre-existing
mismatch, not one this Flatpak introduces.

---

## Before submitting to Flathub

**The app ID is a blocker.** `org.montech.HyperFlow` claims the domain
`montech.org`. Flathub requires that you control the domain in your app ID,
and this project does not — worse, an `org.montech.*` ID actively implies the
vendor affiliation that `NOTICE.md` exists to disclaim. Flathub review will
reject it. Rename to something you do control, e.g.
`io.github.<owner>.HyperFlow`, and change in lockstep: the manifest filename
and `id`, the metainfo filename and `<id>`, and the desktop/icon/metainfo
renames in the app module's build commands. (The polkit action id
`org.montech.hyperflow.configure` is a separate namespace and can stay, though
the same argument applies to it.)

Also required:

- **Pin a `commit:` on every git source.** flatpak-builder accepts a bare
  `tag:`; the Flathub linter does not. `git ls-remote <url> <tag>` for each.
- **Screenshots.** The metainfo deliberately ships none — none exist in the
  repo, and pointing at a URL that 404s is worse than omitting the tag.
  Flathub's quality checks want at least one. Take one of the panel menu.
- **A developer ID.** The metainfo omits `<developer id=…>` because
  AppStream rejects the uppercase `OWNER` placeholder and an `org.montech.*`
  ID would assert the affiliation being disclaimed. Fill in a real one.
- **Bump the runtime.** `runtime-version: '48'` is pinned, not tracking.
  GNOME runtimes go EOL roughly a year after release. Check current with
  `flatpak remote-ls flathub --system | grep org.gnome.Platform` and rebuild
  everything after changing it — libdbusmenu has broken on runtime bumps
  before.
- **Say the quiet part in the listing.** The store description must keep the
  "unofficial, not affiliated with Montech" wording, and it should say that
  the native daemon package is required. A Flatpak that silently does nothing
  on a clean system earns one-star reviews it cannot answer.

---

## What was verified, and what was not

Verified by actually running it on the machine this was written on:

- `org.montech.HyperFlow.yaml` parses with PyYAML 6.0.3, and every
  `build-command` was extracted from the parsed YAML and executed against a
  staged tree — including the heredoc that generates the shims, which comes
  out with `"$@"` intact and mode 0755.
- `make install-tray PREFIX=/app` produces the expected tree, and the build
  commands then correctly remove the polkit directory, install the docs,
  duplicate the icons under the app-ID name, rename the desktop file with
  `Icon=` rewritten, and rewrite the metainfo `<launchable>`.
- The generated `org.montech.HyperFlow.desktop` passes
  `desktop-file-validate` with no output.
- The generated shims pass `sh -n`.
- `../appstream/org.montech.HyperFlow.metainfo.xml` is well-formed
  (`xml.dom.minidom`) and passes `appstreamcli validate --no-net --pedantic`
  (AppStream 1.1.2) — one pedantic hint and one info, both explained in
  comments in the file itself.

**Not verified, because it could not be:**

- **Nothing was built.** `flatpak` and `flatpak-builder` are not installed
  here. The manifest has never been fed to flatpak-builder.
- **The four AppIndicator modules.** URLs, tags and buildsystems are from
  knowledge. No commit hashes are pinned and none were invented — a wrong
  guess that fails loudly at fetch time is better than a fabricated hash that
  looks authoritative.
- **`appstreamcli compose`**, which is what flatpak-builder runs at the end of
  a build, is not installed (it is a separate addon,
  `org.freedesktop.appstream.compose`). Icon resolution and the
  desktop/metainfo merge are therefore unchecked.
- **Every runtime claim about the sandbox** — that `--filesystem=/run/…:ro`
  binds where expected, that the SNI bus name really cannot be declared, that
  the shims are found by `shutil.which()` in the real runtime. These are
  reasoned from the code and from how Flatpak works, not observed.
- `runtime-version: '48'` was chosen as a conservative, certainly-existing
  GNOME branch. It was not checked against Flathub.

# montech-hyperflow.spec -- RPM packaging for Fedora / RHEL / openSUSE.
#
# Build straight from a release tarball, which is what `make rpm` does:
#
#     make tarball                                  # git archive, prefix montech-hyperflow-1.0.0/
#     rpmbuild -ta montech-hyperflow-1.0.0.tar.gz   # this spec is inside the tarball
#
# `rpmbuild -ta` pulls the spec out of the tarball itself, so this file must
# stay tracked in git or the tarball will not contain it.

# The single source of truth for the version is
# src/montech_hyperflow/__init__.py; the Makefile scrapes __version__ from
# there to name the tarball, and %%prep below fails the build if this drifts
# from it.
%global upstream_version 1.0.0

# The private libdir. NOT %%{_libdir}: this package is noarch and the Makefile
# installs to $(PREFIX)/lib/montech-hyperflow, which is /usr/lib even on
# x86_64 where %%{_libdir} would expand to /usr/lib64.
%global privlibdir %{_prefix}/lib/%{name}

# systemd-rpm-macros gives us %%{_unitdir}, %%{_udevrulesdir}, %%{_tmpfilesdir}
# and friends -- but there is no macro for the system-sleep hook directory on
# any distro, so it is spelled out. Like the unit dir it is /usr/lib even on
# 64-bit multilib systems, hence %%{_prefix}/lib and not %%{_libdir}.
%global sleepdir %{_prefix}/lib/systemd/system-sleep

# Fallbacks so the spec still parses on an older rpm or a distro that ships
# systemd without the macro package.
%{!?_udevrulesdir: %global _udevrulesdir %{_prefix}/lib/udev/rules.d}
%{!?_pkgdocdir:    %global _pkgdocdir    %{_docdir}/%{name}}

# Do not byte-compile. The whole point of the private libdir is that the
# package is not tied to one distro Python: a version-tagged .pyc
# (cpython-313.pyc) in a noarch package would be dead weight the moment the
# system Python moves on, and rpm would then own stale files. Python falls
# back to compiling in memory, which costs a few milliseconds at start-up.
# Both knobs are set because the two distro families spell it differently;
# each is a no-op where it does not apply.
%global _python_bytecompile_extra 0
%undefine __brp_python_bytecompile

Name:           montech-hyperflow
Version:        %{upstream_version}
Release:        1%{?dist}
Summary:        Unofficial Linux driver for the Montech HyperFlow Digital AIO display

License:        GPL-3.0-or-later
URL:            https://github.com/OWNER/montech-hyperflow-linux
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

# Pure Python, no compiled objects, no arch-specific paths.
BuildArch:      noarch

BuildRequires:  make
# python3 is needed at build time twice over: the Makefile runs it at parse
# time to scrape VERSION, and %%check runs the test suite with it.
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros

Requires:       python3 >= 3.8
Requires:       systemd
# pkexec itself. The polkit *policy* ships in -tray (it is the tray that calls
# the helper), but /usr/bin/montech-hyperflow-admin lives here and is only
# meaningfully invoked through pkexec.
Requires:       polkit
%if 0%{?suse_version}
Requires:       udev
Requires(pre):  shadow
%else
Requires:       systemd-udev
Requires(pre):  shadow-utils
%endif
# Requires(post)/(preun)/(postun) on systemd, for the scriptlets below.
%{?systemd_requires}

%description
An unofficial, community-written Linux driver for the 7-segment display on the
pump head of a Montech HyperFlow Digital 241.0.0 all-in-one liquid cooler. It
is NOT produced, endorsed, supported or approved by Montech, TCOMAS or SEMICO;
do not contact them about it. "Montech" and "HyperFlow" are used here only to
identify the hardware the software talks to.

The hardware is USB 1a2c:4e85 on the TCOMAS DH-C100 OEM platform, so rebranded
coolers built on the same pump head very likely work unchanged.

The protocol was recovered by interoperability reverse engineering of the
vendor's freely distributed Windows application and corrected against the
device's own HID report descriptor. No vendor code, firmware, artwork or
strings are shipped or required.

This package contains the daemon and command-line tool, the udev rule that
makes the display writable without root, a systemd service, and a
system-sleep hook that blanks the head before suspend. It is standard library
Python only -- no hidapi, no libusb, no pyudev, no kernel module. The service
is not enabled on install; turn it on with
"systemctl enable --now montech-hyperflow.service".

%package tray
Summary:        Panel indicator for the unofficial montech-hyperflow driver
BuildArch:      noarch
# No %%{?_isa} here on purpose: this is a noarch package, and an ISA-qualified
# dependency can never be satisfied by one.
Requires:       %{name} = %{version}-%{release}
Requires:       python3-gobject
Requires:       gtk3
# Provides the AyatanaAppIndicator3 0.1 typelib. The tray also accepts the
# newer AyatanaAppIndicatorGLib 1 or the legacy AppIndicator3 0.1 at runtime,
# but this is the binding that is actually packaged on Fedora today.
%if 0%{?suse_version}
Requires:       typelib-1_0-AyatanaAppIndicator3-0_1
Requires:       typelib-1_0-Gtk-3_0
%else
Requires:       libayatana-appindicator-gtk3
%endif
# Owns /usr/share/icons/hicolor and its subdirectories.
Requires:       hicolor-icon-theme

%description tray
A GTK 3 / AppIndicator panel indicator for montech-hyperflow, the unofficial
community driver for the Montech HyperFlow Digital AIO pump-head display
(USB 1a2c:4e85, TCOMAS DH-C100 OEM platform). Not affiliated with, endorsed by
or supported by Montech; all icons here are original work and no vendor
artwork is used.

It shows the current temperature in the panel and lets you switch source and
units, writing system settings through a polkit-authenticated helper.

This is a separate package so a headless machine can install the driver
without pulling in GTK.

%prep
# -n matches the Makefile's `git archive --prefix=$(NAME)-$(VERSION)/`.
%autosetup -n %{name}-%{version}

# Guard against this spec drifting from the source of truth. Cheap here, very
# annoying to debug later when the tarball name and the package version stop
# agreeing.
grep -q '^__version__ = "%{version}"$' src/montech_hyperflow/__init__.py

%build
# Nothing to build: the package is pure Python and the /usr/bin wrappers are
# generated during `make install-*`.

%install
# The Makefile is the contract; the overrides only exist where an RPM macro
# names a different directory than the Makefile's default:
#   UDEVDIR  the Makefile defaults to /etc/udev/rules.d, but a distro package
#            must ship vendor rules in %%{_udevrulesdir} (/usr/lib/udev/rules.d)
#            and leave /etc free for the admin to override them. The 72-
#            prefix still does its job there: udev merges both directories
#            into one list sorted by file name, so the rule is still evaluated
#            before /usr/lib/udev/rules.d/73-seat-late.rules consumes the
#            uaccess tag.
#   DOCDIR   openSUSE puts docs under /usr/share/doc/packages/<name>.
#   UNITDIR/SLEEPDIR  same value as the Makefile default today; passed so the
#            paths in %%files cannot silently disagree with what was installed.
# reload-udev, which install-core depends on, is a no-op whenever DESTDIR is
# set, so it does nothing here.
#
# Plain make, not %%make_install (which would append its own `install` target
# to this command line) and not %%make_build (whose -j would let the shared
# install-lib prerequisite of install-core and install-tray run twice at
# once). INSTALL="install -p" keeps mtimes for reproducible builds.
%{__make} install-core install-tray \
    DESTDIR=%{buildroot} \
    INSTALL="install -p" \
    PREFIX=/usr \
    UDEVDIR=%{_udevrulesdir} \
    UNITDIR=%{_unitdir} \
    SLEEPDIR=%{sleepdir} \
    DOCDIR=%{_pkgdocdir}

# The unit is generated from montech-hyperflow.service.in with @BINDIR@ and
# @DOCDIR@ substituted from PREFIX, so PREFIX=/usr above already yields
# ExecStart=%{_bindir}/montech-hyperflow. Asserted, not rewritten: the unit is
# Type=exec, so a stale path is a 203/EXEC failure on every start, and failing
# the build is far cheaper than shipping it.
grep -q '^ExecStart=%{_bindir}/%{name} ' %{buildroot}%{_unitdir}/%{name}.service

# /etc/montech-hyperflow.conf is written by montech-hyperflow-admin, not
# shipped -- the driver has working defaults. It is listed as %%ghost
# %%config(noreplace) so rpm knows the path belongs to this package and will
# not clobber a file the admin helper wrote. Created empty here only so
# rpmbuild has something to stat; %%ghost keeps it out of the payload, so no
# empty file is ever installed on the target.
install -d %{buildroot}%{_sysconfdir}
: > %{buildroot}%{_sysconfdir}/%{name}.conf

%check
# Hardware-free unit tests; they must not need a device or root.
%make_build test

%pre
# The systemd unit runs as DynamicUser=yes with SupplementaryGroups=plugdev,
# which is the group the udev rule puts on the hidraw node. systemd refuses to
# start a unit whose SupplementaryGroups= does not resolve, and unlike
# Debian/Ubuntu, Fedora and openSUSE do not ship a plugdev group -- so without
# this the service fails at start with a resolution error.
#
# Tradeoff: this creates an empty system group with a name that carries
# meaning elsewhere ("may access pluggable devices" on Debian). Creating it
# grants nobody anything by itself, but an admin who later adds users to
# plugdev is granting access to this device along with whatever else is keyed
# on that name locally. The alternative -- a dedicated montech group -- would
# mean patching both the udev rule and the unit and diverging from the Debian
# package for no functional gain, so the shared name wins.
#
# The group is deliberately never removed on uninstall: rpm cannot know
# whether another package, a local udev rule or a user account still refers
# to it, and deleting a group that is still referenced is far worse than
# leaving an empty one behind.
getent group plugdev >/dev/null || groupadd -r plugdev || :

%post
# %%systemd_post applies the *preset* policy on first install; it does not
# force-enable anything. The default policy on Fedora and openSUSE is
# "disable", so the service stays off until the user opts in with
# `systemctl enable --now montech-hyperflow.service` -- which is the intended
# behaviour here -- while an admin who ships a preset that enables it still
# gets what they asked for. Omitting the macro would break that second case.
# An explicit daemon-reload is not needed: systemd's own rpm file triggers
# fire one whenever files under %%{_unitdir} change.
%systemd_post %{name}.service

# A plain `udevadm control --reload` only affects devices that appear
# afterwards, so the trigger is what fixes permissions on a cooler that is
# already plugged in -- which it always is, being watercooling. Both calls are
# tolerant of failure: scriptlets also run in containers and image builds
# where there is no running udevd.
udevadm control --reload >/dev/null 2>&1 || :
udevadm trigger --subsystem-match=hidraw --action=add >/dev/null 2>&1 || :

%preun
%systemd_preun %{name}.service

%postun
# Restart a running daemon on upgrade so new code takes effect. The unit's
# --blank-on-exit means the head goes dark for one restart cycle and comes
# back; that is preferable to running the old code until the next reboot.
# Does nothing if the service is not running.
%systemd_postun_with_restart %{name}.service

if [ $1 -eq 0 ]; then
    # Erase: drop the rule from udevd's in-memory set. No trigger here on
    # purpose -- re-running the (now absent) rule cannot restore default
    # ownership on an existing node, and the next replug does it correctly.
    udevadm control --reload >/dev/null 2>&1 || :
fi

%post tray
# Icon cache. Current Fedora and RHEL regenerate the hicolor cache from an rpm
# file trigger in gtk-update-icon-cache, which makes these three scriptlets
# redundant there -- but they are harmless, and openSUSE and older RHEL still
# expect the package to do it. There is no update-desktop-database call
# because the .desktop file declares no MimeType, which is the only thing that
# database indexes.
touch --no-create %{_datadir}/icons/hicolor >/dev/null 2>&1 || :

%postun tray
if [ $1 -eq 0 ]; then
    touch --no-create %{_datadir}/icons/hicolor >/dev/null 2>&1 || :
    gtk-update-icon-cache %{_datadir}/icons/hicolor >/dev/null 2>&1 || :
fi

%posttrans tray
gtk-update-icon-cache %{_datadir}/icons/hicolor >/dev/null 2>&1 || :

%files
# LICENSE and the docs are installed by the Makefile's install-doc into
# %%{_pkgdocdir}; marking the absolute paths keeps the Makefile the only place
# that decides where files go.
%license %{_pkgdocdir}/LICENSE
%dir %{_pkgdocdir}
%doc %{_pkgdocdir}/README.md
%doc %{_pkgdocdir}/NOTICE.md
%doc %{_pkgdocdir}/PROTOCOL.md
%doc %{_pkgdocdir}/EXPERIMENTS.md
%doc %{_pkgdocdir}/montech-hyperflow.conf.example
%{_bindir}/%{name}
%dir %{privlibdir}
%dir %{privlibdir}/montech_hyperflow
# Only the top-level modules; montech_hyperflow/tray/ belongs to -tray.
%{privlibdir}/montech_hyperflow/*.py
%{_udevrulesdir}/72-%{name}.rules
%{_unitdir}/%{name}.service
# systemd silently ignores a sleep hook that is not executable, which is a
# miserable thing to debug, so the mode is asserted here as well as in the
# Makefile.
%attr(0755,root,root) %{sleepdir}/%{name}
%ghost %config(noreplace) %{_sysconfdir}/%{name}.conf

%files tray
%{_bindir}/%{name}-tray
# The pkexec helper travels with the polkit action that authorises it: an
# action whose annotated exec.path does not exist makes polkit log errors, and
# a helper with no action falls back to a generic prompt. Both or neither.
%{_bindir}/%{name}-admin
%{privlibdir}/montech_hyperflow/tray/
%{_datadir}/applications/%{name}-tray.desktop
%{_datadir}/polkit-1/actions/org.montech.hyperflow.policy
# Four names in three size directories. 16x16/ and 24x24/ carry the reduced
# drawing and scalable/ the full one; -light is the trim for light panels.
# All twelve are generated by packaging/icons/generate.py.
%{_datadir}/icons/hicolor/16x16/apps/%{name}.svg
%{_datadir}/icons/hicolor/16x16/apps/%{name}-idle.svg
%{_datadir}/icons/hicolor/16x16/apps/%{name}-light.svg
%{_datadir}/icons/hicolor/16x16/apps/%{name}-light-idle.svg
%{_datadir}/icons/hicolor/24x24/apps/%{name}.svg
%{_datadir}/icons/hicolor/24x24/apps/%{name}-idle.svg
%{_datadir}/icons/hicolor/24x24/apps/%{name}-light.svg
%{_datadir}/icons/hicolor/24x24/apps/%{name}-light-idle.svg
%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg
%{_datadir}/icons/hicolor/scalable/apps/%{name}-idle.svg
%{_datadir}/icons/hicolor/scalable/apps/%{name}-light.svg
%{_datadir}/icons/hicolor/scalable/apps/%{name}-light-idle.svg

%changelog
* Thu Sep 10 2026 montech-hyperflow packagers <OWNER@users.noreply.github.com> - 1.0.0-1
- Initial RPM packaging, split into montech-hyperflow and -tray.
- Ship the udev rule in %%{_udevrulesdir} rather than /etc, leaving /etc for
  admin overrides.
- Create the plugdev group in %%pre; the unit's SupplementaryGroups= needs it
  and Fedora does not ship one.
- Assert the unit's ExecStart matches %%{_bindir}. The unit is generated from
  montech-hyperflow.service.in with @BINDIR@ substituted from PREFIX, so no
  rewrite is needed here -- only the check that it stayed correct.

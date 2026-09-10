#!/bin/sh
# Build a montech-hyperflow AppImage from this source tree.
#
#   packaging/appimage/build-appimage.sh [OUTDIR]
#
# OUTDIR defaults to build/ at the top of the tree. The AppDir is populated
# entirely through the Makefile's DESTDIR/PREFIX contract, the same one the
# .deb, the RPM and the snap use, so the bundle contains exactly the files a
# distro package would -- minus the host-integration pieces an AppImage is not
# allowed to install (see README.md next to this script).
#
# appimagetool is NOT downloaded by this script: a build step that fetches an
# unpinned binary over the network is not something to hide inside a package
# build. Install it yourself and put it on PATH, or set APPIMAGETOOL=/path/to
# /appimagetool-x86_64.AppImage. If it is missing, the AppDir is still built
# and the exact command to finish the job is printed.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)

outdir=${1:-$root/build}
version=$(make --no-print-directory -C "$root" version)
# appimagetool picks its runtime by $ARCH and refuses to guess.
ARCH=${ARCH:-$(uname -m)}
export ARCH

appdir=$outdir/montech-hyperflow.AppDir
target=$outdir/montech-hyperflow-$version-$ARCH.AppImage

echo "montech-hyperflow $version -> $target"

rm -rf "$appdir"
mkdir -p "$outdir"

# install-lib      the package itself
# install-icons    hicolor icons; the AppImage's own icon is taken from here
# install-desktop  the .desktop the AppImage runtime requires
# install-doc      README/NOTICE/LICENSE/PROTOCOL, so the bundle is
#                  self-describing and ships its own licence text
# install-udev     into the AppDir only. montech-hyperflow-setup reads the
#                  rule back out of the AppDir when the user runs it as root;
#                  nothing here touches the host's /etc.
#
# Deliberately NOT built: install-unit (an AppImage cannot install a systemd
# unit or a sleep hook), install-polkit (nor a polkit action), and install-bin
# (the generated /usr/bin wrappers hardcode an absolute
# /usr/lib/montech-hyperflow and would shadow the AppDir with a system install
# of a different version; AppRun launches the package out of the AppDir
# directly instead).
make --no-print-directory -C "$root" \
    install-lib install-icons install-desktop install-doc install-udev \
    DESTDIR="$appdir" PREFIX=/usr

install -d "$appdir/usr/bin"
install -m 0755 "$here/montech-hyperflow-setup" "$appdir/usr/bin/montech-hyperflow-setup"
install -m 0755 "$here/AppRun" "$appdir/AppRun"

# The AppDir root needs: the .desktop file, an icon file named after its
# Icon= key, and .DirIcon for file managers and the thumbnailer.
cp "$appdir/usr/share/applications/montech-hyperflow-tray.desktop" \
   "$appdir/montech-hyperflow-tray.desktop"
cp "$appdir/usr/share/icons/hicolor/scalable/apps/montech-hyperflow.svg" \
   "$appdir/montech-hyperflow.svg"
ln -sf montech-hyperflow.svg "$appdir/.DirIcon"

# AppStream metadata is optional and belongs to the distro-packaging side of
# the tree. Ship it if it is there.
for meta in "$root"/packaging/appstream/*.metainfo.xml \
            "$root"/packaging/appstream/*.appdata.xml; do
    [ -f "$meta" ] || continue
    install -d "$appdir/usr/share/metainfo"
    install -m 0644 "$meta" "$appdir/usr/share/metainfo/"
    echo "  + $(basename "$meta")"
done

# Fail here rather than shipping a bundle that cannot import itself.
for required in \
    "$appdir/AppRun" \
    "$appdir/montech-hyperflow-tray.desktop" \
    "$appdir/.DirIcon" \
    "$appdir/usr/bin/montech-hyperflow-setup" \
    "$appdir/usr/lib/montech-hyperflow/montech_hyperflow/cli.py" \
    "$appdir/usr/lib/montech-hyperflow/montech_hyperflow/tray/app.py" \
    "$appdir/etc/udev/rules.d/72-montech-hyperflow.rules"
do
    if [ ! -e "$required" ]; then
        echo "build-appimage.sh: AppDir is incomplete, missing $required" >&2
        exit 1
    fi
done
echo "AppDir ready: $appdir"

tool=${APPIMAGETOOL:-appimagetool}
if ! command -v "$tool" >/dev/null 2>&1; then
    cat >&2 <<EOF

build-appimage.sh: appimagetool not found, so no .AppImage was produced.

The AppDir above is complete. To finish:

  1. get appimagetool from
     https://github.com/AppImage/appimagetool/releases
     (or your distro's appimagetool / AppImageKit package)
  2. ARCH=$ARCH appimagetool "$appdir" "$target"

Or re-run this script with APPIMAGETOOL pointing at it:

  APPIMAGETOOL=~/Downloads/appimagetool-$ARCH.AppImage $0 $outdir

EOF
    exit 1
fi

"$tool" "$appdir" "$target"
echo "built $target"

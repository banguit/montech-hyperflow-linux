# montech-hyperflow
#
#   make test                 hardware-free test suite
#   make lint                 syntax-check everything shipped
#   sudo make install         daemon + udev + service + sleep hook + docs
#   sudo make install-tray    the panel indicator (needs PyGObject/GTK3)
#   sudo make uninstall       remove everything
#   make deb / rpm / tarball  build packages locally

PREFIX      ?= /usr/local
DESTDIR     ?=
BINDIR      ?= $(PREFIX)/bin
LIBDIR      ?= $(PREFIX)/lib/montech-hyperflow
DOCDIR      ?= $(PREFIX)/share/doc/montech-hyperflow
DATADIR     ?= $(PREFIX)/share
SYSCONFDIR  ?= /etc
UDEVDIR     ?= $(SYSCONFDIR)/udev/rules.d
UNITDIR     ?= /usr/lib/systemd/system
SLEEPDIR    ?= /usr/lib/systemd/system-sleep
POLKITDIR   ?= $(DATADIR)/polkit-1/actions
APPDIR      ?= $(DATADIR)/applications
ICONDIR     ?= $(DATADIR)/icons/hicolor

PYTHON      ?= python3
NAME        := montech-hyperflow
VERSION_FROM_SOURCE := $(shell $(PYTHON) -c "import re;print(re.search(r'__version__ = \"([^\"]+)\"',open('src/montech_hyperflow/__init__.py').read()).group(1))")
VERSION     ?= $(VERSION_FROM_SOURCE)
INSTALL     ?= install

# The package is installed to a private libdir with generated wrappers rather
# than into site-packages. It is one fewer thing to get wrong per distro, it
# does not break when the system Python is upgraded, and it makes the AppImage
# and the .deb identical.
#
# $(call wrapper,<dest>,<module>)
define wrapper
	sed -e 's|@PYTHON@|$(PYTHON)|g' -e 's|@LIBDIR@|$(LIBDIR)|g' \
	    -e 's|@ENTRY@|$(2)|g' packaging/wrapper.in > $(1)
	chmod 0755 $(1)
endef

.PHONY: all test check lint bump set-repo check-version install install-core install-tray install-bin \
        install-lib install-udev install-unit install-polkit install-doc \
        install-icons install-desktop uninstall enable disable reload-udev \
        deb rpm tarball clean version

all:
	@echo "$(NAME) $(VERSION)"
	@echo "targets: test lint install install-tray uninstall deb rpm tarball"

version:
	@echo $(VERSION)

test check:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -v

lint:
	PYTHONPATH=src $(PYTHON) -m compileall -q src tests
	@command -v udevadm >/dev/null && udevadm verify packaging/udev/*.rules || echo "  (udevadm verify unavailable)"
	@command -v desktop-file-validate >/dev/null && desktop-file-validate packaging/desktop/*.desktop || echo "  (desktop-file-validate unavailable)"
	@$(PYTHON) -c "import xml.dom.minidom as m;m.parse('packaging/polkit/org.montech.hyperflow.policy.in');print('  polkit policy OK')"
	@for f in packaging/icons/*/apps/*.svg; do $(PYTHON) -c "import xml.dom.minidom,sys;xml.dom.minidom.parse(sys.argv[1])" "$$f" || exit 1; done
	@# Valid XML is not enough: gdk-pixbuf sniffs the image format from the
	@# first bytes of the file, so an SVG whose root element is pushed past
	@# that window by a leading comment loads fine in an XML parser and then
	@# fails at runtime with "couldn't recognize the image file format".
	@for f in packaging/icons/*/apps/*.svg; do \
	    off=$$($(PYTHON) -c "import sys;print(open(sys.argv[1],'rb').read().find(b'<svg'))" "$$f"); \
	    if [ "$$off" -lt 0 ] || [ "$$off" -gt 128 ]; then \
	        echo "  FAIL $$f: <svg> starts at byte $$off; keep it under 128 (move comments inside the element)"; \
	        exit 1; \
	    fi; \
	done
	@$(PYTHON) packaging/icons/generate.py >/dev/null && git diff --quiet -- packaging/icons 2>/dev/null \
	    || echo "  note: packaging/icons is out of sync with generate.py (or not a git tree)"
	@$(PYTHON) -c "import gi;gi.require_version('GdkPixbuf','2.0');from gi.repository import GdkPixbuf;import glob;[GdkPixbuf.Pixbuf.new_from_file_at_scale(f,16,16,True) for f in glob.glob('packaging/icons/*/apps/*.svg')];print('  icons OK (parse + 16px load + generator in sync)')" 2>/dev/null || echo "  icons OK (parse only; no GdkPixbuf here)"
	@sh -n packaging/systemd/montech-hyperflow-sleep && echo "  sleep hook OK"

install: install-core
	@echo
	@echo "Installed $(NAME) $(VERSION)."
	@echo "  montech-hyperflow --list      check the device is writable"
	@echo "  sudo make enable              start at boot"
	@echo "  sudo make install-tray        add the panel indicator"

install-core: install-lib install-bin install-udev install-unit install-doc reload-udev

install-lib:
	$(INSTALL) -d $(DESTDIR)$(LIBDIR)/montech_hyperflow/tray
	$(INSTALL) -m 0644 src/montech_hyperflow/*.py $(DESTDIR)$(LIBDIR)/montech_hyperflow/
	$(INSTALL) -m 0644 src/montech_hyperflow/tray/*.py $(DESTDIR)$(LIBDIR)/montech_hyperflow/tray/

install-bin:
	$(INSTALL) -d $(DESTDIR)$(BINDIR)
	$(call wrapper,$(DESTDIR)$(BINDIR)/$(NAME),cli)
	$(call wrapper,$(DESTDIR)$(BINDIR)/$(NAME)-admin,admin)

install-tray: install-lib install-icons install-desktop install-polkit
	$(INSTALL) -d $(DESTDIR)$(BINDIR)
	$(call wrapper,$(DESTDIR)$(BINDIR)/$(NAME)-tray,tray.app)
	@if [ -z "$(DESTDIR)" ] && command -v gtk-update-icon-cache >/dev/null; then \
	    gtk-update-icon-cache -f -t $(ICONDIR) 2>/dev/null || true; fi
	@echo "Tray installed. Run: $(NAME)-tray"

# 16x16/ and 24x24/ carry the reduced drawing, scalable/ the full one; the
# icon theme picks by requested size. Icons are generated -- see
# packaging/icons/generate.py -- so do not hand-edit the SVGs.
install-icons:
	$(INSTALL) -d $(DESTDIR)$(ICONDIR)/16x16/apps $(DESTDIR)$(ICONDIR)/24x24/apps \
	             $(DESTDIR)$(ICONDIR)/scalable/apps
	$(INSTALL) -m 0644 packaging/icons/16x16/apps/*.svg $(DESTDIR)$(ICONDIR)/16x16/apps/
	$(INSTALL) -m 0644 packaging/icons/24x24/apps/*.svg $(DESTDIR)$(ICONDIR)/24x24/apps/
	$(INSTALL) -m 0644 packaging/icons/scalable/apps/*.svg $(DESTDIR)$(ICONDIR)/scalable/apps/

install-desktop:
	$(INSTALL) -d $(DESTDIR)$(APPDIR)
	$(INSTALL) -m 0644 packaging/desktop/$(NAME)-tray.desktop $(DESTDIR)$(APPDIR)/

install-polkit:
	$(INSTALL) -d $(DESTDIR)$(POLKITDIR)
	sed -e 's|@BINDIR@|$(BINDIR)|g' \
	    packaging/polkit/org.montech.hyperflow.policy.in \
	    > $(DESTDIR)$(POLKITDIR)/org.montech.hyperflow.policy
	chmod 0644 $(DESTDIR)$(POLKITDIR)/org.montech.hyperflow.policy

install-udev:
	$(INSTALL) -d $(DESTDIR)$(UDEVDIR)
	$(INSTALL) -m 0644 packaging/udev/72-$(NAME).rules $(DESTDIR)$(UDEVDIR)/

install-unit:
	$(INSTALL) -d $(DESTDIR)$(UNITDIR) $(DESTDIR)$(SLEEPDIR)
	sed -e 's|@BINDIR@|$(BINDIR)|g' -e 's|@DOCDIR@|$(DOCDIR)|g' \
	    packaging/systemd/$(NAME).service.in \
	    > $(DESTDIR)$(UNITDIR)/$(NAME).service
	chmod 0644 $(DESTDIR)$(UNITDIR)/$(NAME).service
	$(INSTALL) -m 0755 packaging/systemd/$(NAME)-sleep $(DESTDIR)$(SLEEPDIR)/$(NAME)

install-doc:
	$(INSTALL) -d $(DESTDIR)$(DOCDIR)
	$(INSTALL) -m 0644 README.md NOTICE.md LICENSE docs/PROTOCOL.md \
	    docs/EXPERIMENTS.md packaging/$(NAME).conf.example $(DESTDIR)$(DOCDIR)/

# Re-applying to already-created nodes needs an explicit trigger; a plain
# `udevadm control --reload` only affects devices that appear afterwards.
reload-udev:
	@if [ -z "$(DESTDIR)" ] && command -v udevadm >/dev/null; then \
	    udevadm control --reload; \
	    udevadm trigger --subsystem-match=hidraw --action=add; \
	    udevadm settle; \
	fi

enable:
	systemctl daemon-reload
	systemctl enable --now $(NAME).service
	systemctl --no-pager status $(NAME).service || true

disable:
	-systemctl disable --now $(NAME).service

uninstall:
	-systemctl disable --now $(NAME).service
	rm -f  $(DESTDIR)$(BINDIR)/$(NAME) $(DESTDIR)$(BINDIR)/$(NAME)-tray \
	       $(DESTDIR)$(BINDIR)/$(NAME)-admin
	rm -rf $(DESTDIR)$(LIBDIR)
	rm -f  $(DESTDIR)$(UDEVDIR)/72-$(NAME).rules
	rm -f  $(DESTDIR)$(UDEVDIR)/99-$(NAME).rules
	rm -f  $(DESTDIR)$(UNITDIR)/$(NAME).service
	rm -f  $(DESTDIR)$(SLEEPDIR)/$(NAME)
	rm -f  $(DESTDIR)$(POLKITDIR)/org.montech.hyperflow.policy
	rm -f  $(DESTDIR)$(APPDIR)/$(NAME)-tray.desktop
	rm -f  $(DESTDIR)$(ICONDIR)/16x16/apps/$(NAME)*.svg
	rm -f  $(DESTDIR)$(ICONDIR)/24x24/apps/$(NAME)*.svg
	rm -f  $(DESTDIR)$(ICONDIR)/scalable/apps/$(NAME)*.svg
	rm -f  $(DESTDIR)$(ICONDIR)/symbolic/apps/$(NAME)*.svg
	rm -rf $(DESTDIR)$(DOCDIR)
	@if [ -z "$(DESTDIR)" ]; then systemctl daemon-reload; \
	    udevadm control --reload 2>/dev/null || true; fi

tarball:
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ \
	    -o $(NAME)-$(VERSION).tar.gz HEAD
	@echo "built $(NAME)-$(VERSION).tar.gz"

deb: test
	rm -rf build/deb
	$(MAKE) install-core install-tray DESTDIR=build/deb PREFIX=/usr
	install -d build/deb/DEBIAN
	sed -e 's/@VERSION@/$(VERSION)/' packaging/debian/control.in > build/deb/DEBIAN/control
	install -m 0755 packaging/debian/postinst build/deb/DEBIAN/postinst
	install -m 0755 packaging/debian/postrm   build/deb/DEBIAN/postrm
	install -m 0644 packaging/debian/conffiles build/deb/DEBIAN/conffiles
	dpkg-deb --root-owner-group --build build/deb build/$(NAME)_$(VERSION)_all.deb
	@echo "built build/$(NAME)_$(VERSION)_all.deb"

rpm: tarball
	rpmbuild -ta $(NAME)-$(VERSION).tar.gz

# The version and the repository owner appear in packaging metadata, docs and
# CI in dozens of places. Both are scripted so they cannot drift: the source
# of truth for the version is src/montech_hyperflow/__init__.py, and `bump`
# rewrites everything that must agree with it.
#
#   make bump VERSION=1.0.1
#   make set-repo OWNER=yourname
bump:
	@test -n "$(VERSION)" || { echo "usage: make bump VERSION=x.y.z"; exit 2; }
	@echo "$(VERSION)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$$' \
	    || { echo "error: VERSION must look like x.y.z"; exit 2; }
	@old=$(VERSION_FROM_SOURCE); \
	 sed -i 's/^__version__ = ".*"$$/__version__ = "$(VERSION)"/' src/montech_hyperflow/__init__.py; \
	 grep -rl "$$old" --exclude-dir=.git --exclude-dir=build . \
	   | xargs -r sed -i "s/$$old/$(VERSION)/g"; \
	 echo "bumped $$old -> $(VERSION)"
	@$(MAKE) --no-print-directory check-version

# REPO is optional and only needed if the repository is not named
# montech-hyperflow-linux. Note the repository name and the INSTALLED names
# differ on purpose: the repo says what it is for a human browsing GitHub,
# while the binary, the package, the systemd unit and the config file are all
# plain "montech-hyperflow".
set-repo:
	@test -n "$(OWNER)" || { echo "usage: make set-repo OWNER=yourname [REPO=reponame]"; exit 2; }
	@if [ -n "$(REPO)" ] && [ "$(REPO)" != "montech-hyperflow-linux" ]; then \
	    grep -rl 'montech-hyperflow-linux' --exclude-dir=.git --exclude-dir=build . \
	      | xargs -r sed -i 's|montech-hyperflow-linux|$(REPO)|g'; \
	    echo "repository name set to $(REPO)"; \
	fi
	@grep -rl 'OWNER' --exclude-dir=.git --exclude-dir=build . \
	   | xargs -r sed -i 's|OWNER|$(OWNER)|g'
	@echo "repository owner set to $(OWNER)"
	@echo "remaining OWNER placeholders: $$(grep -ro OWNER --exclude-dir=.git --exclude-dir=build . | wc -l)"

# Fails if any packaging file disagrees with the source of truth.
check-version:
	@v=$(VERSION_FROM_SOURCE); rc=0; \
	 check() { grep -q "$$2" "$$1" || { echo "  MISMATCH $$1 (expected $$v)"; rc=1; }; }; \
	 check packaging/arch/PKGBUILD "^pkgver=$$v$$"; \
	 check packaging/rpm/montech-hyperflow.spec "^%global upstream_version $$v$$"; \
	 check packaging/debian-source/changelog "($$v-1)"; \
	 check packaging/appstream/org.montech.HyperFlow.metainfo.xml "version=\"$$v\""; \
	 test $$rc -eq 0 && echo "  version $$v consistent across packaging"; \
	 exit $$rc

clean:
	rm -rf build __pycache__ */__pycache__ */*/__pycache__ *.pyc \
	       $(NAME)-*.tar.gz
	find . -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

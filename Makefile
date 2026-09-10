# montech-hyperflow — install/uninstall
#
#   make test               run the hardware-free test suite
#   sudo make install       install driver, udev rule, unit, sleep hook, docs
#   sudo make install-udev  udev rule only (enough for foreground use)
#   sudo make enable        enable + start the service
#   sudo make uninstall     remove everything
#   make deb                build a minimal .deb

PREFIX      ?= /usr/local
DESTDIR     ?=
BINDIR      ?= $(PREFIX)/bin
DOCDIR      ?= $(PREFIX)/share/doc/montech-hyperflow
SYSCONFDIR  ?= /etc
UDEVDIR     ?= $(SYSCONFDIR)/udev/rules.d
UNITDIR     ?= /usr/lib/systemd/system
SLEEPDIR    ?= /usr/lib/systemd/system-sleep

PYTHON      ?= python3
VERSION     := $(shell $(PYTHON) -c "import re;print(re.search(r'__version__ = \"([^\"]+)\"',open('montech-hyperflow.py').read()).group(1))")

INSTALL     ?= install

.PHONY: all test check install install-bin install-udev install-unit \
        install-doc uninstall enable disable reload-udev deb clean lint

all:
	@echo "montech-hyperflow $(VERSION)"
	@echo "targets: test, install, install-udev, enable, uninstall, deb"

test check:
	$(PYTHON) -m unittest -v test_montech

lint:
	$(PYTHON) -m py_compile montech-hyperflow.py test_montech.py
	@command -v udevadm >/dev/null && udevadm verify 72-montech-hyperflow.rules || true
	@sh -n montech-hyperflow-sleep

install: install-bin install-udev install-unit install-doc reload-udev
	@echo
	@echo "Installed montech-hyperflow $(VERSION)."
	@echo "Next:  montech-hyperflow --list          (check the device is writable)"
	@echo "       sudo make enable                  (start at boot)"

install-bin:
	$(INSTALL) -d $(DESTDIR)$(BINDIR)
	$(INSTALL) -m 0755 montech-hyperflow.py $(DESTDIR)$(BINDIR)/montech-hyperflow

install-udev:
	$(INSTALL) -d $(DESTDIR)$(UDEVDIR)
	$(INSTALL) -m 0644 72-montech-hyperflow.rules $(DESTDIR)$(UDEVDIR)/

install-unit:
	$(INSTALL) -d $(DESTDIR)$(UNITDIR) $(DESTDIR)$(SLEEPDIR)
	$(INSTALL) -m 0644 montech-hyperflow.service $(DESTDIR)$(UNITDIR)/
	$(INSTALL) -m 0755 montech-hyperflow-sleep $(DESTDIR)$(SLEEPDIR)/montech-hyperflow

install-doc:
	$(INSTALL) -d $(DESTDIR)$(DOCDIR)
	$(INSTALL) -m 0644 README.md PROTOCOL.md EXPERIMENTS.md \
	    montech-hyperflow.conf.example $(DESTDIR)$(DOCDIR)/

# Re-applying to already-created nodes needs an explicit trigger; a plain
# `udevadm control --reload` only affects devices that appear afterwards.
reload-udev:
	@if [ -z "$(DESTDIR)" ]; then \
	    udevadm control --reload; \
	    udevadm trigger --subsystem-match=hidraw --action=add; \
	    udevadm settle; \
	fi

enable:
	systemctl daemon-reload
	systemctl enable --now montech-hyperflow.service
	systemctl --no-pager status montech-hyperflow.service || true

disable:
	-systemctl disable --now montech-hyperflow.service

uninstall:
	-systemctl disable --now montech-hyperflow.service
	rm -f $(DESTDIR)$(BINDIR)/montech-hyperflow
	rm -f $(DESTDIR)$(UDEVDIR)/72-montech-hyperflow.rules
	rm -f $(DESTDIR)$(UDEVDIR)/99-montech-hyperflow.rules
	rm -f $(DESTDIR)$(UNITDIR)/montech-hyperflow.service
	rm -f $(DESTDIR)$(SLEEPDIR)/montech-hyperflow
	rm -rf $(DESTDIR)$(DOCDIR)
	@if [ -z "$(DESTDIR)" ]; then \
	    systemctl daemon-reload; \
	    udevadm control --reload; \
	fi

deb: test
	rm -rf build/deb
	$(MAKE) install-bin install-udev install-unit install-doc \
	    DESTDIR=build/deb PREFIX=/usr
	install -d build/deb/DEBIAN
	printf 'Package: montech-hyperflow\nVersion: %s\nSection: utils\nPriority: optional\nArchitecture: all\nDepends: python3 (>= 3.8)\nMaintainer: local build <root@localhost>\nDescription: Montech HyperFlow Digital pump-head display driver\n Drives the 7-segment display on a Montech HyperFlow Digital AIO pump head\n from Linux over hidraw, with no third-party dependencies.\n' \
	    "$(VERSION)" > build/deb/DEBIAN/control
	printf '#!/bin/sh\nset -e\nudevadm control --reload || true\nudevadm trigger --subsystem-match=hidraw --action=add || true\n' \
	    > build/deb/DEBIAN/postinst
	chmod 0755 build/deb/DEBIAN/postinst
	dpkg-deb --build build/deb build/montech-hyperflow_$(VERSION)_all.deb
	@echo "built build/montech-hyperflow_$(VERSION)_all.deb"

clean:
	rm -rf build __pycache__ *.pyc

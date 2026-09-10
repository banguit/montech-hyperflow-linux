"""The tray indicator.

Shows the temperature the pump head is currently displaying, in the panel,
and offers the handful of controls that are worth having one click away.

It is a CLIENT. It never opens the hidraw device -- the system daemon owns
that. Two writers to one display would fight at 1 Hz.
"""

import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import GLib, Gtk                             # noqa: E402

from .. import __version__                                      # noqa: E402
from .. import config as configmod                              # noqa: E402
from . import service                                           # noqa: E402

APP_ID = "montech-hyperflow"
ICON_ACTIVE = "montech-hyperflow"
ICON_IDLE = "montech-hyperflow-idle"
REFRESH_MS = 1000

DOC_DIRS = ("/usr/share/doc/montech-hyperflow",
            "/usr/local/share/doc/montech-hyperflow")


def _thermometer_fallback():
    """Icon name to use when our themed icon is not installed."""
    theme = Gtk.IconTheme.get_default()
    for name in ("temperature-symbolic", "sensors-temperature-symbolic",
                 "utilities-system-monitor-symbolic", "computer-symbolic"):
        if theme.has_icon(name):
            return name
    return "application-x-executable"


class TrayApp:
    def __init__(self):
        theme = Gtk.IconTheme.get_default()
        self.icon_active = (ICON_ACTIVE if theme.has_icon(ICON_ACTIVE)
                            else _thermometer_fallback())
        self.icon_idle = (ICON_IDLE if theme.has_icon(ICON_IDLE)
                          else self.icon_active)

        self.indicator = AppIndicator.Indicator.new(
            APP_ID, self.icon_active,
            AppIndicator.IndicatorCategory.HARDWARE)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Montech HyperFlow")

        self._building = False
        self.menu = Gtk.Menu()
        self.indicator.set_menu(self.menu)

        self.last_signature = None
        self.build_menu(None, "unknown")
        self.refresh()
        GLib.timeout_add(REFRESH_MS, self._tick)

    # -- panel ------------------------------------------------------------

    def _tick(self):
        self.refresh()
        return True

    def refresh(self):
        record = service.read_status()
        state = service.unit_state()

        if record and not record.get("stale") and record.get("connected"):
            shown = record.get("displayed")
            unit = record.get("unit", "C")
            label = "--" if shown is None else "%d°%s" % (shown, unit)
            self.indicator.set_icon_full(self.icon_active, "connected")
            tooltip = "%s %s  ·  %s" % (
                (record.get("source") or "cpu").upper(), label,
                record.get("device") or "?")
        else:
            label = ""
            self.indicator.set_icon_full(self.icon_idle, "not running")
            tooltip = {"active": "starting…",
                       "inactive": "service stopped",
                       "failed": "service failed",
                       "not-installed": "service not installed"}.get(
                           state, "no status from the daemon")
        self.indicator.set_label(label, "888°C")
        self.indicator.set_title("Montech HyperFlow — " + tooltip)

        # Rebuild the menu only when something a user can see has changed,
        # so an open menu is not yanked out from under the pointer.
        signature = (state, bool(record), record and record.get("unit"),
                     record and record.get("source"),
                     record and record.get("stale"))
        if signature != self.last_signature:
            self.last_signature = signature
            self.build_menu(record, state)
        else:
            self._update_header(record, state)

    # -- menu -------------------------------------------------------------

    def _update_header(self, record, state):
        if not hasattr(self, "header"):
            return
        self.header.set_label(self._header_text(record, state))

    @staticmethod
    def _header_text(record, state):
        if record and not record.get("stale"):
            if record.get("blanked"):
                return "Display blanked — %s" % record["blanked"]
            shown = record.get("displayed")
            unit = record.get("unit", "C")
            src = (record.get("source") or "cpu").upper()
            if shown is None:
                return "%s — no reading" % src
            return "%s  %d °%s   (level %s)" % (
                src, shown, unit, record.get("level"))
        return {"active": "Service running, no status yet",
                "inactive": "Service stopped",
                "failed": "Service failed",
                "not-installed": "Service not installed"}.get(
                    state, "Daemon not running")

    def build_menu(self, record, state):
        self._building = True
        for child in self.menu.get_children():
            self.menu.remove(child)

        self.header = Gtk.MenuItem(label=self._header_text(record, state))
        self.header.set_sensitive(False)
        self.menu.append(self.header)

        if record and record.get("sensor"):
            detail = Gtk.MenuItem(label="   %s" % record["sensor"])
            detail.set_sensitive(False)
            self.menu.append(detail)

        self.menu.append(Gtk.SeparatorMenuItem())

        unit = (record or {}).get("unit", "C")
        for code, text in (("C", "Celsius"), ("F", "Fahrenheit")):
            item = Gtk.CheckMenuItem(label=text)
            item.set_draw_as_radio(True)
            item.set_active(unit == code)
            item.connect("toggled", self.on_unit, code)
            self.menu.append(item)

        self.menu.append(Gtk.SeparatorMenuItem())

        source = (record or {}).get("source", "cpu")
        for code, text in (("cpu", "Show CPU"), ("gpu", "Show GPU")):
            item = Gtk.CheckMenuItem(label=text)
            item.set_draw_as_radio(True)
            item.set_active(source == code)
            item.connect("toggled", self.on_source, code)
            self.menu.append(item)

        self.menu.append(Gtk.SeparatorMenuItem())

        running = state == "active"
        toggle = Gtk.MenuItem(
            label="Stop display service" if running else "Start display service")
        toggle.connect("activate", self.on_toggle_service, running)
        toggle.set_sensitive(state != "not-installed")
        self.menu.append(toggle)

        blank = Gtk.MenuItem(label="Blank the display now")
        blank.connect("activate", self.on_blank)
        self.menu.append(blank)

        self.menu.append(Gtk.SeparatorMenuItem())

        docs = Gtk.MenuItem(label="Protocol notes")
        docs.connect("activate", self.on_docs)
        self.menu.append(docs)

        about = Gtk.MenuItem(label="About")
        about.connect("activate", self.on_about)
        self.menu.append(about)

        quit_item = Gtk.MenuItem(label="Quit tray")
        quit_item.connect("activate", self.on_quit)
        self.menu.append(quit_item)

        self.menu.show_all()
        self._building = False

    # -- actions ----------------------------------------------------------

    def _report(self, ok, message, success=None):
        if ok:
            if success:
                self._dialog(Gtk.MessageType.INFO, success)
        else:
            self._dialog(Gtk.MessageType.ERROR, message or "The action failed.")
        self.last_signature = None       # force a rebuild on the next tick
        self.refresh()

    def _dialog(self, kind, text):
        dialog = Gtk.MessageDialog(transient_for=None, modal=False,
                                   message_type=kind,
                                   buttons=Gtk.ButtonsType.CLOSE, text=text)
        dialog.connect("response", lambda d, _r: d.destroy())
        dialog.show()

    def on_unit(self, item, code):
        if self._building or not item.get_active():
            return
        ok, out = service.apply_settings(fahrenheit=(code == "F"))
        self._report(ok, out)

    def on_source(self, item, code):
        if self._building or not item.get_active():
            return
        ok, out = service.apply_settings(source=code)
        self._report(ok, out)

    def on_toggle_service(self, _item, running):
        ok, out = service.stop() if running else service.start()
        self._report(ok, out)

    def on_blank(self, _item):
        # The daemon owns the device while it is running, so ask it to stand
        # down first rather than racing it.
        was_active = service.unit_state() == "active"
        if was_active:
            service.stop()
        ok, out = service._run(["montech-hyperflow", "--blank"])
        self._report(ok, out)

    def on_docs(self, _item):
        for directory in DOC_DIRS:
            path = os.path.join(directory, "PROTOCOL.md")
            if os.path.exists(path):
                Gtk.show_uri_on_window(None, "file://" + path, 0)
                return
        self._dialog(Gtk.MessageType.INFO,
                     "Protocol notes are not installed on this system.")

    def on_about(self, _item):
        dialog = Gtk.AboutDialog()
        dialog.set_program_name("Montech HyperFlow")
        dialog.set_version(__version__)
        dialog.set_comments(
            "Drives the 7-segment display on a Montech HyperFlow Digital "
            "AIO pump head.\n\nUnofficial community driver, not affiliated "
            "with or endorsed by Montech.\nHardware: USB 1a2c:4e85, OEM "
            "platform TCOMAS DH-C100.")
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.set_logo_icon_name(self.icon_active)
        dialog.connect("response", lambda d, _r: d.destroy())
        dialog.show()

    def on_quit(self, _item):
        # Only the tray exits. The daemon keeps driving the head.
        Gtk.main_quit()


def main(argv=None):
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print("montech-hyperflow-tray needs a graphical session.",
              file=sys.stderr)
        return 1
    TrayApp()
    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

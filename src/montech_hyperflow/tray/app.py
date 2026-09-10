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


def _load_appindicator():
    """The indicator binding, newest first.

    libayatana-appindicator3 warns that it is deprecated in favour of
    libayatana-appindicator-glib, but the GLib variant is not yet packaged on
    every distro (it is absent on Ubuntu 26.04). AppIndicator3 is the older
    Ubuntu name, still present on some systems. Try them in order.
    """
    for name, version in (("AyatanaAppIndicatorGLib", "1"),
                          ("AyatanaAppIndicator3", "0.1"),
                          ("AppIndicator3", "0.1")):
        try:
            gi.require_version(name, version)
            module = __import__("gi.repository", fromlist=[name])
            return getattr(module, name)
        except (ValueError, ImportError, AttributeError):
            continue
    raise ImportError(
        "no AppIndicator binding found. Install one of:\n"
        "  Debian/Ubuntu  gir1.2-ayatanaappindicator3-0.1\n"
        "  Fedora         libayatana-appindicator-gtk3\n"
        "  Arch           libayatana-appindicator")


AppIndicator = _load_appindicator()
from gi.repository import GLib, Gtk                             # noqa: E402

from .. import __version__                                      # noqa: E402
from .. import config as configmod                              # noqa: E402
from . import service                                           # noqa: E402

APP_ID = "montech-hyperflow"

# Panel icons are the SYMBOLIC variants, in preference order. A symbolic icon
# is recoloured by the shell to match the panel foreground, which is what
# makes it correct on both light and dark themes and crisp at 16px. The
# full-colour icon is for the About dialog and the app grid, where it sits on
# a known background at a size where detail reads.
ICON_ACTIVE = ("montech-hyperflow-symbolic", "montech-hyperflow")
ICON_IDLE = ("montech-hyperflow-idle-symbolic", "montech-hyperflow-idle",
             "montech-hyperflow-symbolic", "montech-hyperflow")
ICON_APP = "montech-hyperflow"
REFRESH_MS = 1000

DOC_DIRS = ("/usr/share/doc/montech-hyperflow",
            "/usr/local/share/doc/montech-hyperflow")


def _thermometer_fallback():
    """Stock icon to use when ours is not installed.

    Symbolic names only: an unthemed full-colour stock icon in a panel looks
    worse than a generic symbolic one.
    """
    theme = Gtk.IconTheme.get_default()
    for name in ("temperature-symbolic", "sensors-temperature-symbolic",
                 "utilities-system-monitor-symbolic", "computer-symbolic"):
        if theme.has_icon(name):
            return name
    return "application-x-executable"


class TrayApp:
    def __init__(self):
        theme = Gtk.IconTheme.get_default()

        def pick(names):
            for name in names:
                if theme.has_icon(name):
                    return name
            return _thermometer_fallback()

        self.icon_active = pick(ICON_ACTIVE)
        self.icon_idle = pick(ICON_IDLE)
        self.icon_app = (ICON_APP if theme.has_icon(ICON_APP)
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
        self.build_menu(None, "unknown", {})
        self.refresh()
        GLib.timeout_add(REFRESH_MS, self._tick)

    # -- panel ------------------------------------------------------------

    def _tick(self):
        self.refresh()
        return True

    def refresh(self):
        record = service.read_status()
        state = service.unit_state()
        settings = service.read_settings()

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
        gpus, gpu_error = service.gpu_choices()
        signature = (state, bool(record), record and record.get("stale"),
                     service.selected_unit(record, settings),
                     service.selected_source(record, settings),
                     service.selected_gpu_index(record, settings),
                     tuple(gpus), gpu_error)
        if signature != self.last_signature:
            self.last_signature = signature
            self.build_menu(record, state, settings)
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

    def build_menu(self, record, state, settings=None):
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

        # Real RadioMenuItems, not CheckMenuItems with set_draw_as_radio().
        # The latter only *looks* like a radio: nothing deselects its sibling,
        # so Celsius and Fahrenheit could both end up ticked.
        settings = settings or {}
        unit = service.selected_unit(record, settings)
        group = None
        for code, text in (("C", "Celsius"), ("F", "Fahrenheit")):
            item = Gtk.RadioMenuItem(label=text)
            if group is None:
                group = item
            else:
                item.join_group(group)
            item.set_active(unit == code)
            item.connect("toggled", self.on_unit, code)
            self.menu.append(item)

        self.menu.append(Gtk.SeparatorMenuItem())

        source = service.selected_source(record, settings)
        gpu_index = service.selected_gpu_index(record, settings)
        gpus, gpu_error = service.gpu_choices()

        group = Gtk.RadioMenuItem(label="Show CPU")
        group.set_active(source == "cpu")
        group.connect("toggled", self.on_source, "cpu", None)
        self.menu.append(group)

        # One entry per GPU that actually works, named. "Show GPU" alone is
        # ambiguous on a two-card machine, and offering it at all when no GPU
        # source is reachable just puts the service into 78/CONFIG on click.
        for index, name in gpus:
            label = "Show %s" % name if index is None else \
                    "Show GPU %d - %s" % (index, name)
            item = Gtk.RadioMenuItem(label=label)
            item.join_group(group)
            item.set_active(source == "gpu"
                            and (index is None or index == gpu_index))
            item.connect("toggled", self.on_source, "gpu", index)
            self.menu.append(item)

        if not gpus:
            item = Gtk.MenuItem(label="GPU unavailable - %s"
                                      % (gpu_error or "no GPU found"))
            item.set_sensitive(False)
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

    def on_source(self, item, code, gpu_index=None):
        if self._building or not item.get_active():
            return
        settings = {"source": code}
        if code == "gpu" and gpu_index is not None:
            settings["gpu_index"] = gpu_index
        ok, out = service.apply_settings(**settings)
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
        dialog.set_logo_icon_name(self.icon_app)
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

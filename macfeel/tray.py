"""macfeel-tray: top-bar icon + menu, and GNOME mouse-settings handling.

GTK3 + AyatanaAppIndicator (Ubuntu's AppIndicator extension shows it).
Runs in the user's session; talks to the daemon over the control socket.

    python3 -m macfeel.tray
"""

import os
import signal
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import GLib, Gtk  # noqa: E402

from .gnome import GnomeMouse  # noqa: E402
from .uilink import DaemonLink, data_dir, package_parent  # noqa: E402

RESTORE_DELAY = 3  # s; don't flip GNOME settings back for a brief Bluetooth reconnect

# Reasons for which a device row gets a usable checkbox (see devices.classify).
TOGGLEABLE = ("disabled by user", "combined keyboard/mouse device")


class Tray:
    def __init__(self):
        self.status = None
        self.connected = False
        self.gnome = GnomeMouse()
        self.gnome_active = False
        self.restore_timer = None

        self.indicator = AppIndicator.Indicator.new(
            "macfeel", "macfeel-error-symbolic", AppIndicator.IndicatorCategory.HARDWARE)
        self.indicator.set_icon_theme_path(os.path.join(data_dir(), "icons"))
        self.indicator.set_title("MacFeel")
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="Connecting…", sensitive=False)
        self.toggle_item = Gtk.MenuItem(label="Pause")
        self.toggle_item.connect("activate", self.on_toggle)
        self.devices_item = Gtk.MenuItem(label="Mice")
        self.devices_menu = Gtk.Menu()
        self.devices_item.set_submenu(self.devices_menu)
        self.warn_item = Gtk.MenuItem(label="⚠ GNOME acceleration is on (stacking) — Fix")
        self.warn_item.connect("activate", lambda _: self.gnome.make_flat())
        settings_item = Gtk.MenuItem(label="Settings…")
        settings_item.connect("activate", lambda _: open_settings())
        quit_item = Gtk.MenuItem(label="Quit tray icon")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        for item in (self.status_item, self.toggle_item, Gtk.SeparatorMenuItem(),
                     self.devices_item, self.warn_item, Gtk.SeparatorMenuItem(),
                     settings_item, quit_item):
            menu.append(item)
        menu.show_all()
        self.indicator.set_menu(menu)

        self.gnome.settings.connect("changed::accel-profile", lambda *_: self.update())
        self.link = DaemonLink(["status"], self.on_message, self.on_connected)
        self.update()

    # --- daemon events ---------------------------------------------------

    def on_connected(self, connected):
        self.connected = connected
        if not connected:
            self.status = None
        self.update()

    def on_message(self, msg):
        if msg.get("event") == "status":
            self.status = msg
            self.update()

    # --- actions ---------------------------------------------------------

    def on_toggle(self, _item):
        if self.status:
            self.link.request("set_enabled", enabled=not self.status["enabled"])

    def on_device_toggled(self, item, device):
        settings = {"enabled": item.get_active()}
        if item.get_active():
            settings["allow_combined"] = True
        self.link.request("set_device", id=device["id"], settings=settings)

    # --- state -----------------------------------------------------------

    def update(self):
        st = self.status
        grabbed = st["grabbed"] if st else 0
        if not self.connected or st is None:
            icon, text = "macfeel-error-symbolic", "MacFeel is not running"
        elif not st["enabled"]:
            icon, text = "macfeel-paused-symbolic", "Paused"
        elif grabbed == 0:
            icon, text = "macfeel-error-symbolic", "Active — no mouse found"
        else:
            icon, text = "macfeel-active-symbolic", f"Active — {grabbed} mouse" + ("s" if grabbed > 1 else "")
        self.indicator.set_icon_full(icon, text)
        self.status_item.set_label(text)
        self.toggle_item.set_label("Resume" if st and not st["enabled"] else "Pause")
        self.toggle_item.set_sensitive(st is not None)
        self.rebuild_devices(st["devices"] if st else [])
        self.sync_gnome(bool(st and st["enabled"] and grabbed))
        self.warn_item.set_visible(self.gnome_active and not self.gnome.is_flat())

    def rebuild_devices(self, devices):
        for child in self.devices_menu.get_children():
            self.devices_menu.remove(child)
        for dev in devices:
            toggleable = dev["grabbed"] or dev["reason"] in TOGGLEABLE
            if toggleable:
                item = Gtk.CheckMenuItem(label=dev["name"])
                item.set_active(dev["grabbed"])
                item.connect("toggled", self.on_device_toggled, dev)
            else:
                item = Gtk.MenuItem(label=f"{dev['name']} — {dev['reason']}", sensitive=False)
            self.devices_menu.append(item)
        if not devices:
            self.devices_menu.append(Gtk.MenuItem(label="No mice", sensitive=False))
        self.devices_menu.show_all()
        self.devices_item.set_sensitive(self.connected)

    def sync_gnome(self, should_be_active):
        """Flat while MacFeel shapes the pointer; the user's own settings otherwise."""
        if should_be_active:
            if self.restore_timer:
                GLib.source_remove(self.restore_timer)
                self.restore_timer = None
            if not self.gnome_active:
                self.gnome.activate()
                self.gnome_active = True
        elif self.gnome_active and not self.restore_timer:
            self.restore_timer = GLib.timeout_add_seconds(RESTORE_DELAY, self._restore)

    def _restore(self):
        self.restore_timer = None
        self.gnome.restore()
        self.gnome_active = False
        self.update()
        return False


def open_settings():
    """Start the settings window (it's single-instance: a second start just raises it)."""
    subprocess.Popen([sys.executable, "-m", "macfeel.settings"], cwd=package_parent(),
                     start_new_session=True)


def main():
    Tray()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Gtk.main()


if __name__ == "__main__":
    main()

"""macfeel-tray: top-bar icon + menu, and GNOME mouse-settings handling.

Uses libayatana-appindicator-glib (no GUI toolkit at all): the menu is a
Gio.Menu, its items trigger Gio actions, and Ubuntu's AppIndicator extension
draws it. Runs in the user's session; talks to the daemon over the socket.

    python3 -m macfeel.tray
"""

import os
import signal
import subprocess
import sys

import gi

gi.require_version("AyatanaAppIndicatorGlib", "2.0")
from gi.repository import AyatanaAppIndicatorGlib as AppIndicator  # noqa: E402
from gi.repository import Gio, GLib, GLibUnix  # noqa: E402

from .gnome import GnomeMouse  # noqa: E402
from .uilink import DaemonLink, data_dir, package_parent  # noqa: E402

RESTORE_DELAY = 3  # s; don't flip GNOME settings back for a brief Bluetooth reconnect
ACTION_PREFIX = "indicator."

# Reasons for which a device gets a usable checkbox (see devices.classify).
TOGGLEABLE = ("disabled by user", "combined keyboard/mouse device")


class Tray:
    def __init__(self, loop):
        self.loop = loop
        self.status = None
        self.connected = False
        self.gnome = GnomeMouse()
        self.gnome_active = False
        self.restore_timer = None
        self.device_actions = []

        self.actions = Gio.SimpleActionGroup()
        for name, callback in (("toggle", self.on_toggle), ("settings", lambda *_: open_settings()),
                               ("quit", lambda *_: self.loop.quit()),
                               ("fix-gnome", lambda *_: self.gnome.make_flat())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.actions.add_action(action)
        self.menu = Gio.Menu()

        self.indicator = AppIndicator.Indicator.new(
            "macfeel", "macfeel-error-symbolic", AppIndicator.IndicatorCategory.HARDWARE)
        self.indicator.set_icon_theme_path(os.path.join(data_dir(), "icons"))
        self.indicator.set_title("MacFeel")
        self.indicator.set_actions(self.actions)
        self.indicator.set_menu(self.menu)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        self.gnome.settings.connect("changed::accel-profile", lambda *_: self.update())
        self.link = DaemonLink(["status"], self.on_message, self.on_connected)
        self.update()
        self.link.start()

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

    def on_toggle(self, *_):
        if self.status:
            self.link.request("set_enabled", enabled=not self.status["enabled"])

    def on_device_toggled(self, action, value, device):
        settings = {"enabled": value.get_boolean()}
        if value.get_boolean():
            settings["allow_combined"] = True
        action.set_state(value)
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
        self.indicator.set_icon(icon, text)
        self.indicator.set_tooltip(icon, "MacFeel", text)
        self.actions.lookup_action("toggle").set_enabled(st is not None)

        self.sync_gnome(bool(st and st["enabled"] and grabbed))
        stacking = self.gnome_active and not self.gnome.is_flat()
        self.rebuild_menu(text, st, stacking)

    def rebuild_menu(self, text, st, stacking):
        for name in self.device_actions:
            self.actions.remove_action(name)
        self.device_actions = []

        top = Gio.Menu()
        top.append(text, None)   # no action: shown as a plain, disabled line
        top.append("Resume" if st and not st["enabled"] else "Pause", ACTION_PREFIX + "toggle")
        if stacking:
            top.append("⚠ GNOME acceleration is on (stacking) — Fix", ACTION_PREFIX + "fix-gnome")

        mice = Gio.Menu()
        for i, dev in enumerate(st["devices"] if st else []):
            name = f"device-{i}"
            toggleable = dev["id"] and (dev["grabbed"] or dev["reason"] in TOGGLEABLE)
            action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", dev["grabbed"]))
            action.set_enabled(bool(toggleable))
            action.connect("change-state", self.on_device_toggled, dev)
            self.actions.add_action(action)
            self.device_actions.append(name)
            label = dev["name"] if toggleable else f"{dev['name']} — {dev['reason']}"
            mice.append(label, ACTION_PREFIX + name)
        if not mice.get_n_items():
            mice.append("No mice", None)

        bottom = Gio.Menu()
        bottom.append("Settings…", ACTION_PREFIX + "settings")
        bottom.append("Quit tray icon", ACTION_PREFIX + "quit")

        self.menu.remove_all()
        self.menu.append_section(None, top)
        self.menu.append_submenu("Mice", mice)
        self.menu.append_section(None, bottom)

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
    """Start the settings window (single instance: a second start just raises it)."""
    subprocess.Popen([sys.executable, "-m", "macfeel.settings"], cwd=package_parent(),
                     start_new_session=True)


def main():
    loop = GLib.MainLoop()
    Tray(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, loop.quit)
    loop.run()


if __name__ == "__main__":
    main()

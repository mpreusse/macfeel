"""macfeel-tray: top-bar icon + menu, and GNOME mouse-settings handling.

No GUI toolkit: the icon and menu are published over D-Bus by sni.py
(StatusNotifierItem + dbusmenu), which Ubuntu's AppIndicator extension draws.
Runs in the user's session; talks to the daemon over the socket.

    python3 -m macfeel.tray
"""

import os
import signal
import subprocess
import sys

import gi

from gi.repository import Gio, GLib  # noqa: E402

try:                                    # GLib >= 2.80
    from gi.repository import GLibUnix  # noqa: E402
    unix_signal_add = GLibUnix.signal_add
except ImportError:
    unix_signal_add = GLib.unix_signal_add

from .gnome import GnomeMouse  # noqa: E402
from .sni import MenuItem, StatusNotifierItem  # noqa: E402
from .uilink import DaemonLink, data_dir, package_parent  # noqa: E402

RESTORE_DELAY = 3  # s; don't flip GNOME settings back for a brief Bluetooth reconnect

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
        self.notified = set()        # device ids we already sent a "captured" notification for
        self.indicator = StatusNotifierItem("macfeel", os.path.join(data_dir(), "icons"), "MacFeel")

        self.gnome.on_profile_changed(self.update)
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

    def on_toggle(self):
        if self.status:
            self.link.request("set_enabled", enabled=not self.status["enabled"])

    def on_device_toggled(self, device):
        enable = not device["grabbed"]
        settings = {"enabled": enable}
        if enable:
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
        busy = [d for d in (st["devices"] if st else []) if d.get("busy_by")]
        if busy and st["enabled"] and grabbed == 0:
            icon, text = "macfeel-error-symbolic", f"{busy[0]['name']} is captured by {busy[0]['busy_by']}"
        self.notify_captured(busy)
        self.indicator.set_icon(icon, text)

        self.sync_gnome(bool(st and st["enabled"] and grabbed))
        stacking = self.gnome_active and not self.gnome.is_flat()
        self.rebuild_menu(text, st, stacking, busy)

    def notify_captured(self, busy):
        """One desktop notification per mouse when another program captures it."""
        busy_ids = {d["id"] for d in busy}
        for dev in busy:
            if dev["id"] not in self.notified:
                send_notification(
                    f"MacFeel can't use your {dev['name']}",
                    f"{dev['busy_by']} has captured it, so it moves without the Mac curve. "
                    "Open MacFeel's settings to see how to fix this.")
        self.notified = busy_ids   # forget mice that are no longer captured

    def rebuild_menu(self, text, st, stacking, busy):
        items = [
            MenuItem(text),   # no callback: shown as a plain, disabled line
            MenuItem("Resume" if st and not st["enabled"] else "Pause",
                     self.on_toggle if st else None),
        ]
        if stacking:
            items.append(MenuItem("⚠ GNOME acceleration is on (stacking) — Fix", self.gnome.make_flat))
        for dev in busy:
            items.append(MenuItem(f"⚠ {dev['name']} is captured by {dev['busy_by']} — how to fix…",
                                  open_settings))

        mice = []
        for dev in st["devices"] if st else []:
            toggleable = dev["id"] and (dev["grabbed"] or dev["reason"] in TOGGLEABLE)
            if toggleable:
                mice.append(MenuItem(dev["name"], lambda d=dev: self.on_device_toggled(d),
                                     checked=dev["grabbed"]))
            else:
                mice.append(MenuItem(f"{dev['name']} — {dev['reason']}"))
        items.append(MenuItem("Mice", children=mice or [MenuItem("No mice")]))

        items += [MenuItem.separator(),
                  MenuItem("Settings…", open_settings),
                  MenuItem("Quit tray icon", self.loop.quit)]
        self.indicator.set_menu(items)

    def sync_gnome(self, should_be_active):
        """Flat while MacFeel shapes the pointer; the user's own settings otherwise."""
        if should_be_active:
            if self.restore_timer:
                GLib.source_remove(self.restore_timer)
                self.restore_timer = None
            if not self.gnome_active:
                # Mark first: changing the setting fires "changed", which calls update() again.
                self.gnome_active = True
                self.gnome.activate()
        elif self.gnome_active and not self.restore_timer:
            self.restore_timer = GLib.timeout_add_seconds(RESTORE_DELAY, self._restore)

    def _restore(self):
        self.restore_timer = None
        self.gnome_active = False   # first, for the same reason as in sync_gnome
        self.gnome.restore()
        self.update()
        return False


def send_notification(summary, body):
    """Desktop notification via the freedesktop D-Bus API (no toolkit needed)."""
    try:
        Gio.bus_get_sync(Gio.BusType.SESSION, None).call(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", ("MacFeel", 0, "input-mouse", summary, body, [], {}, -1)),
            None, Gio.DBusCallFlags.NONE, -1, None, None, None)
    except GLib.Error:
        pass   # no notification daemon: the menu still shows the warning


def open_settings():
    """Start the settings window (single instance: a second start just raises it)."""
    subprocess.Popen([sys.executable, "-m", "macfeel.settings"], cwd=package_parent(),
                     start_new_session=True)


def main():
    loop = GLib.MainLoop()
    Tray(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        unix_signal_add(GLib.PRIORITY_DEFAULT, sig, loop.quit)
    loop.run()


if __name__ == "__main__":
    main()

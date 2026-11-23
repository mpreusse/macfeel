"""GNOME mouse settings: switch libinput to "flat" while MacFeel is active.

GNOME's own acceleration would stack on top of ours, and with the flat
profile its speed slider becomes a plain multiplier (1 + speed), so we set
it to 0 to get exactly the Mac curve. The user's original values are backed
up once and restored when MacFeel is paused, stopped or uninstalled.
Runs in the user's session (gsettings is per user).
"""

import json
import os

from gi.repository import Gio

SCHEMA = "org.gnome.desktop.peripherals.mouse"
BACKUP = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                      "macfeel", "gnome-backup.json")


class GnomeMouse:
    """GNOME's mouse settings. On desktops without them (KDE, ...) every method
    is a no-op: Gio.Settings.new() would abort the process for a missing schema."""

    def __init__(self):
        source = Gio.SettingsSchemaSource.get_default()
        self.available = bool(source and source.lookup(SCHEMA, True))
        self.settings = Gio.Settings.new(SCHEMA) if self.available else None

    def on_profile_changed(self, callback):
        if self.available:
            self.settings.connect("changed::accel-profile", lambda *_: callback())

    def current(self):
        return {"accel-profile": self.settings.get_string("accel-profile"),
                "speed": self.settings.get_double("speed")}

    def is_flat(self):
        return not self.available or self.settings.get_string("accel-profile") == "flat"

    def activate(self):
        """Back up the user's values (only if no backup exists yet), then go flat at speed 0."""
        if not self.available:
            return
        if not os.path.exists(BACKUP):
            os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
            with open(BACKUP, "w") as f:
                json.dump(self.current(), f, indent=2)
        self.make_flat()

    def make_flat(self):
        if not self.available:
            return
        self.settings.set_string("accel-profile", "flat")
        self.settings.set_double("speed", 0.0)
        Gio.Settings.sync()

    def restore(self):
        """Put back what the user had before MacFeel. No-op without a backup."""
        if not self.available:
            return False
        try:
            with open(BACKUP) as f:
                saved = json.load(f)
        except (FileNotFoundError, ValueError):
            return False
        self.settings.set_string("accel-profile", saved.get("accel-profile", "default"))
        self.settings.set_double("speed", float(saved.get("speed", 0.0)))
        Gio.Settings.sync()
        os.unlink(BACKUP)
        return True

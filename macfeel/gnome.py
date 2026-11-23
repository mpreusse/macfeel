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
    def __init__(self):
        self.settings = Gio.Settings.new(SCHEMA)

    def current(self):
        return {"accel-profile": self.settings.get_string("accel-profile"),
                "speed": self.settings.get_double("speed")}

    def is_flat(self):
        return self.settings.get_string("accel-profile") == "flat"

    def activate(self):
        """Back up the user's values (only if no backup exists yet), then go flat at speed 0."""
        if not os.path.exists(BACKUP):
            os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
            with open(BACKUP, "w") as f:
                json.dump(self.current(), f, indent=2)
        self.make_flat()

    def make_flat(self):
        self.settings.set_string("accel-profile", "flat")
        self.settings.set_double("speed", 0.0)
        Gio.Settings.sync()

    def restore(self):
        """Put back what the user had before MacFeel. No-op without a backup."""
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

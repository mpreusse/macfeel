"""GLib glue for the UIs: a self-reconnecting subscription to the daemon."""

import os

from gi.repository import GLib

from . import ipc

RETRY_SECONDS = 2


class DaemonLink:
    """Keeps a subscription open and reconnects when the daemon restarts.

    on_message(msg)      : every pushed event ({"event": "status"|"live", ...})
    on_connected(bool)   : connection state changes
    """

    def __init__(self, topics, on_message, on_connected):
        self.topics = list(topics)
        self.on_message, self.on_connected = on_message, on_connected
        self.client = None
        self.watch = None
        self.connected = False

    def start(self):
        """Connect (callbacks may fire immediately, so call this after storing the link)."""
        self._connect()
        return self

    def _connect(self):
        try:
            client = ipc.Client()
            client.request("subscribe", topics=self.topics)
        except (OSError, ConnectionError, RuntimeError):
            self._set_connected(False)
            GLib.timeout_add_seconds(RETRY_SECONDS, self._retry)
            return
        self.client = client
        self.watch = GLib.io_add_watch(client.fileno(), GLib.PRIORITY_DEFAULT,
                                       GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._readable)
        self._set_connected(True)
        for msg in client.take_pushed():   # e.g. the status sent right after subscribing
            self.on_message(msg)

    def _retry(self):
        self._connect()
        return False   # one-shot; _connect schedules the next try if needed

    def _readable(self, fd, condition):
        try:
            for msg in self.client.read_messages():
                self.on_message(msg)
            return True
        except (OSError, ConnectionError, ValueError):
            self.client.close()
            self.client, self.watch = None, None
            self._set_connected(False)
            GLib.timeout_add_seconds(RETRY_SECONDS, self._retry)
            return False

    def _set_connected(self, value):
        if value != self.connected:
            self.connected = value
            self.on_connected(value)

    def request(self, cmd, **args):
        """One-off request on a fresh connection. Returns None if the daemon is unreachable."""
        try:
            client = ipc.Client()
        except ConnectionError:
            return None
        try:
            return client.request(cmd, **args)
        finally:
            client.close()


def package_parent():
    """Directory that contains the `macfeel` package (for launching sibling processes)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    """Installed data (icons) or the source tree's data/ folder."""
    for candidate in (os.path.join(package_parent(), "data"), "/usr/share/macfeel"):
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(package_parent(), "data")

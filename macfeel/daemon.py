"""macfeeld: finds mice, grabs them, applies the Mac curve, re-emits via uinput.

Run manually during development (you need read access to the mouse and
/dev/uinput, e.g. via the `input` group):

    python3 -m macfeel.daemon --config ./macfeel-dev.json

Ctrl+C (or kill -9) gives the mouse back immediately: the kernel drops the
grab and destroys the uinput twin when our file descriptors close.

Privacy: this process never logs or stores movement data. Logs contain only
lifecycle events (start/stop, device added/removed/skipped, errors).
"""

import argparse
import logging
import os
import selectors
import signal
import sys
import threading
import time

import pyudev

from . import config as cfg
from . import control, curve, devices
from .pipeline import DevicePipeline, PointerSettings

log = logging.getLogger("macfeel")

LOOP_TIMEOUT = 0.2      # s; the loop wakes at least this often (watchdog heartbeat)
WATCHDOG_LIMIT = 0.5    # s; a stalled loop longer than this releases all mice
CONFIG_POLL = 1.0       # s; how often to look for config file changes
RETRY_BUSY = 5.0        # s; retry devices another program had grabbed
LIVE_INTERVAL = 1 / 25  # s; live graph update rate for subscribed UIs
MOVING_WINDOW = 0.15    # s; a mouse counts as "moving" if it reported this recently


class Daemon:
    def __init__(self, config_path, socket_path, allowed_uids):
        self.socket_path, self.allowed_uids = socket_path, allowed_uids
        self.control = None
        self.status_dirty = False
        self.store = cfg.ConfigStore(config_path)
        self.udev = pyudev.Context()
        self.monitor = pyudev.Monitor.from_netlink(self.udev)
        self.monitor.filter_by("input")
        self.sel = selectors.DefaultSelector()
        self.pipelines = {}   # path -> DevicePipeline
        self.status = {}      # path -> dict shown in the UI (grabbed and skipped mice)
        self.running = True
        self.heartbeat = time.monotonic()
        self.lock = threading.Lock()  # pipelines touched by watchdog thread

    # --- device management -------------------------------------------------

    def pointer_settings(self, facts, dev_settings):
        conf = self.store.config
        p, s = conf["pointer"], conf["scroll"]
        # Not the sensor DPI: the resolution macOS assumes for the mouse,
        # which is 400 for mice whose HID descriptor has no physical units.
        dpi = dev_settings.get("dpi") or curve.MAC_DEFAULT_DPI
        return PointerSettings(
            curve=curve.from_config(p, dpi), dpi=dpi, report_rate=p["report_rate"],
            passthrough=not conf["enabled"],
            scroll_enabled=s["enabled"], vertical_strength=s["vertical_strength"],
            horizontal_strength=s["horizontal_strength"])

    def consider(self, path):
        """Look at one event node and grab it if it's a mouse we should handle."""
        if path in self.pipelines:
            return
        try:
            udev_dev = pyudev.Devices.from_device_file(self.udev, path)
        except Exception:
            return
        # Never even open anything udev doesn't call a mouse (keyboards!).
        if udev_dev.properties.get("ID_INPUT_MOUSE") != "1":
            return

        import evdev
        try:
            probe = evdev.InputDevice(path)
        except OSError as e:
            self._set_status(path, None, "skip", devices.describe_open_error(e))
            return
        try:
            facts = devices.gather_facts(self.udev, path, probe)
        finally:
            probe.close()

        dev_id = devices.stable_id(facts)
        dev_settings = cfg.device_settings(self.store.config, dev_id)
        verdict, reason = devices.classify(facts, dev_settings)
        if verdict == devices.IGNORE:
            return
        if verdict == devices.GRAB:
            try:
                pipe = DevicePipeline(path, facts, self.pointer_settings(facts, dev_settings))
            except OSError as e:
                verdict, reason = devices.SKIP, devices.describe_open_error(e)
            else:
                with self.lock:
                    self.pipelines[path] = pipe
                self.sel.register(pipe, selectors.EVENT_READ, lambda mask, p=path: self.on_device(p))
                reason = f"active ({reason})"
        self._set_status(path, facts, verdict, reason)

    def _set_status(self, path, facts, verdict, reason):
        old = self.status.get(path)
        entry = {
            "path": path,
            "id": devices.stable_id(facts) if facts else None,
            "name": facts["name"] if facts else path,
            "grabbed": verdict == devices.GRAB,
            "reason": reason,
            "combined": devices.is_combined(facts) if facts else False,
            "dpi": self.pipelines[path].frame.settings.dpi if path in self.pipelines else None,
            "retry_at": time.monotonic() + RETRY_BUSY if reason.startswith("busy") else None,
        }
        self.status[path] = entry
        self.status_dirty = True
        if not old or old["reason"] != reason:
            log.info("%s %s: %s", "grabbed" if entry["grabbed"] else "skipped", entry["name"], reason)

    def drop(self, path, why, forget=True):
        with self.lock:
            pipe = self.pipelines.pop(path, None)
        if pipe:
            try:
                self.sel.unregister(pipe)
            except (KeyError, ValueError):
                pass
            pipe.close()
            log.info("released %s: %s", pipe.facts["name"], why)
        if forget:
            self.status.pop(path, None)
        self.status_dirty = True

    def on_device(self, path):
        pipe = self.pipelines.get(path)
        if pipe is None:
            return
        try:
            pipe.on_readable()
        except OSError:
            self.drop(path, "device removed")

    def on_udev(self, mask=None):
        for udev_dev in iter(lambda: self.monitor.poll(timeout=0), None):
            node = udev_dev.device_node
            if not node or not node.startswith("/dev/input/event"):
                continue
            if udev_dev.action == "remove":
                self.drop(node, "device removed")
            elif udev_dev.action in ("add", "change"):
                if udev_dev.action == "change":
                    self.drop(node, "device changed")
                self.consider(node)

    def apply_config(self):
        """Push new settings to running pipelines; grab/release only what changed."""
        conf = self.store.config
        for path, pipe in list(self.pipelines.items()):
            dev_settings = cfg.device_settings(conf, devices.stable_id(pipe.facts))
            if devices.classify(pipe.facts, dev_settings)[0] == devices.GRAB:
                pipe.set_settings(self.pointer_settings(pipe.facts, dev_settings))
            else:
                self.drop(path, "disabled", forget=False)
                self.consider(path)
        for path, st in list(self.status.items()):
            if not st["grabbed"]:
                self.consider(path)   # may now be enabled/allowed

    # --- control socket ------------------------------------------------------

    def status_payload(self):
        conf = self.store.config
        devs = [{k: v for k, v in st.items() if k != "retry_at"} for st in self.status.values()]
        return {"enabled": conf["enabled"], "grabbed": len(self.pipelines),
                "devices": sorted(devs, key=lambda d: d["name"])}

    def handle(self, client, msg):
        cmd = msg.get("cmd")
        if cmd in ("status", "devices"):
            payload = self.status_payload()
            return payload if cmd == "status" else payload["devices"]
        if cmd == "get_config":
            return {"config": self.store.config, "defaults": cfg.DEFAULTS}
        if cmd == "set_config":
            self.change_config(msg["changes"])
        elif cmd == "set_enabled":
            self.change_config({"enabled": bool(msg["enabled"])})
        elif cmd == "set_device":
            allowed = {k: v for k, v in msg["settings"].items() if k in cfg.DEVICE_DEFAULTS}
            self.change_config({"devices": {msg["id"]: allowed}})
        elif cmd == "subscribe":
            client.topics.update(t for t in msg.get("topics", []) if t in ("status", "live"))
            if "status" in client.topics:
                client.send({"event": "status", **self.status_payload()})
            return None
        else:
            raise ValueError(f"unknown command {cmd!r}")
        return self.store.config

    def change_config(self, changes):
        if not isinstance(changes, dict):
            raise ValueError("changes must be an object")
        self.store.update(changes)
        self.apply_config()
        self.status_dirty = True

    def live_payload(self):
        """Smoothed speed/gain of the mouse that moved most recently. Memory only, never stored."""
        now = time.monotonic()
        best = None
        for pipe in self.pipelines.values():
            live = pipe.frame.live()
            if live["t"] is not None and (best is None or live["t"] > best[0]["t"]):
                best = (live, pipe.frame.settings)
        if best is None:
            return {"moving": False}
        live, settings = best
        return {"moving": now - live["t"] < MOVING_WINDOW, "counts": live["counts"],
                "gain": live["gain"], "dpi": settings.dpi, "rate": live["rate"]}

    # --- main loop -----------------------------------------------------------

    def watchdog(self):
        """Separate thread: if the event loop hangs, give the mice back and exit."""
        while self.running:
            time.sleep(0.1)
            if time.monotonic() - self.heartbeat > WATCHDOG_LIMIT and self.pipelines:
                log.error("event loop stalled for >%.1fs; releasing all mice and exiting",
                          WATCHDOG_LIMIT)
                with self.lock:
                    for pipe in self.pipelines.values():
                        pipe.ungrab()
                os._exit(1)  # systemd restarts us (Restart=on-failure)

    def run(self):
        self.control = control.ControlServer(self.sel, self.socket_path, self.allowed_uids, self.handle)
        self.monitor.start()
        self.sel.register(self.monitor, selectors.EVENT_READ, self.on_udev)
        for path in devices.list_event_nodes():
            self.consider(path)
        if not self.pipelines:
            log.warning("no mouse grabbed yet; waiting for one to appear")

        threading.Thread(target=self.watchdog, daemon=True).start()
        last_config_check = next_live = time.monotonic()
        while self.running:
            want_live = self.control.wants("live")
            timeout = LIVE_INTERVAL if want_live else LOOP_TIMEOUT
            for key, mask in self.sel.select(timeout=timeout):
                key.data(mask)
            now = time.monotonic()
            self.heartbeat = now
            if want_live and now >= next_live:
                next_live = now + LIVE_INTERVAL
                self.control.broadcast("live", self.live_payload())
            if self.status_dirty:
                self.status_dirty = False
                self.control.broadcast("status", self.status_payload())
            if now - last_config_check > CONFIG_POLL:
                last_config_check = now
                if self.store.changed_on_disk():
                    self.store.load()
                    log.info("config reloaded")
                    self.apply_config()
                for path, st in list(self.status.items()):
                    if st["retry_at"] and now > st["retry_at"]:
                        self.consider(path)
        self.shutdown()

    def shutdown(self):
        for path in list(self.pipelines):
            self.drop(path, "shutting down")
        if self.control:
            self.control.close()
        log.info("stopped")


def default_config_path():
    state = os.environ.get("STATE_DIRECTORY")  # set by systemd StateDirectory=
    return os.path.join(state, "config.json") if state else "./macfeel-dev.json"


def default_socket_path():
    runtime = os.environ.get("RUNTIME_DIRECTORY")  # set by systemd RuntimeDirectory=
    if not runtime:
        runtime = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "macfeel")
    return os.path.join(runtime, "control.sock")


def main(argv=None):
    parser = argparse.ArgumentParser(description="MacFeel daemon")
    parser.add_argument("--config", default=default_config_path())
    parser.add_argument("--socket", default=default_socket_path())
    parser.add_argument("--allow-file", default="/etc/macfeel/allowed-uids",
                        help="UIDs allowed to use the control socket, one per line")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(message)s")

    daemon = Daemon(args.config, args.socket, control.read_allowed_uids(args.allow_file))

    def stop(*_):
        daemon.running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, lambda *_: setattr(daemon.store, "mtime", -1))  # forces reload
    log.info("starting (config: %s)", os.path.abspath(args.config))
    daemon.run()


if __name__ == "__main__":
    main()

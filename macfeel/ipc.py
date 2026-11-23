"""Local control socket: newline-delimited JSON, shared by daemon and UIs.

Request:   {"id": 1, "cmd": "set_config", "changes": {...}}
Response:  {"id": 1, "ok": true, "result": ...}   or {"id": 1, "ok": false, "error": "..."}
Push:      {"event": "status", ...} / {"event": "live", ...}   (after "subscribe")

Commands: status, devices, get_config, set_config, set_enabled,
set_device, subscribe (topics: "status", "live").

Access control: the daemon checks the connecting process's UID with
SO_PEERCRED against an allowlist written at install time.
"""

import json
import os
import socket
import struct

SYSTEM_SOCKET = "/run/macfeel/control.sock"
MAX_LINE = 64 * 1024


def socket_candidates():
    """Where to look for the daemon: explicit override, system service, dev run."""
    if os.environ.get("MACFEEL_SOCKET"):
        return [os.environ["MACFEEL_SOCKET"]]
    paths = [SYSTEM_SOCKET]
    if os.environ.get("XDG_RUNTIME_DIR"):
        paths.append(os.path.join(os.environ["XDG_RUNTIME_DIR"], "macfeel", "control.sock"))
    return paths


def peer_uid(sock):
    pid, uid, gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    return uid


def encode(obj):
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode()


class LineBuffer:
    """Accumulates bytes and yields complete JSON messages."""

    def __init__(self):
        self.buf = b""

    def feed(self, data):
        self.buf += data
        if len(self.buf) > MAX_LINE and b"\n" not in self.buf:
            raise ValueError("message too long")
        *lines, self.buf = self.buf.split(b"\n")
        return [json.loads(line) for line in lines if line.strip()]


class Client:
    """Blocking client for the UIs. Each instance is one connection.

    For subscriptions, call subscribe() and then hand `fileno()` to the GUI
    main loop; when readable, call `read_messages()`.
    """

    def __init__(self, timeout=2.0):
        self.sock = None
        self.timeout = timeout
        self.next_id = 1
        self.reader = LineBuffer()
        self.pushed = []   # events that arrived while waiting for a response
        last_error = None
        for path in socket_candidates():
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            try:
                s.connect(path)
                self.sock = s
                break
            except OSError as e:
                s.close()
                last_error = e
        if self.sock is None:
            raise ConnectionError(f"daemon not reachable: {last_error}")

    def fileno(self):
        return self.sock.fileno()

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def request(self, cmd, **args):
        rid = self.next_id
        self.next_id += 1
        self.sock.sendall(encode({"id": rid, "cmd": cmd, **args}))
        while True:
            for msg in self._recv():
                if msg.get("id") == rid:
                    if not msg.get("ok"):
                        raise RuntimeError(msg.get("error", "request failed"))
                    return msg.get("result")
                self.pushed.append(msg)

    def _recv(self):
        data = self.sock.recv(65536)
        if not data:
            raise ConnectionError("daemon closed the connection")
        return self.reader.feed(data)

    def take_pushed(self):
        msgs, self.pushed = self.pushed, []
        return msgs

    def read_messages(self):
        """Non-blocking-ish read for subscription connections (call when readable)."""
        return self.take_pushed() + self._recv()

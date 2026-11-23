"""Daemon side of the control socket (see ipc.py for the protocol)."""

import errno
import logging
import os
import selectors
import socket

from . import ipc

log = logging.getLogger("macfeel")

MAX_OUTBUF = 256 * 1024   # a client that falls this far behind gets disconnected


def read_allowed_uids(path):
    """Allowlist written by the installer: one UID per line. Root and our own UID always pass."""
    uids = {0, os.getuid()}
    try:
        with open(path) as f:
            uids.update(int(line) for line in f if line.strip().isdigit())
    except FileNotFoundError:
        pass
    return uids


class ClientConn:
    def __init__(self, server, sock, uid):
        self.server, self.sock, self.uid = server, sock, uid
        self.reader = ipc.LineBuffer()
        self.outbuf = b""
        self.topics = set()
        sock.setblocking(False)

    def on_event(self, mask):
        if mask & selectors.EVENT_READ:
            try:
                data = self.sock.recv(65536)
                if not data:
                    return self.close()
                messages = self.reader.feed(data)
            except (OSError, ValueError):
                return self.close()
            for msg in messages:
                self.send(self.server.handle(self, msg))
        if mask & selectors.EVENT_WRITE:
            self.flush()

    def send(self, obj):
        if self.sock is None:
            return
        self.outbuf += ipc.encode(obj)
        if len(self.outbuf) > MAX_OUTBUF:
            return self.close()
        self.flush()

    def flush(self):
        try:
            sent = self.sock.send(self.outbuf) if self.outbuf else 0
            self.outbuf = self.outbuf[sent:]
        except BlockingIOError:
            pass
        except OSError:
            return self.close()
        mask = selectors.EVENT_READ | (selectors.EVENT_WRITE if self.outbuf else 0)
        self.server.sel.modify(self.sock, mask, self.on_event)

    def close(self):
        if self.sock is None:
            return
        try:
            self.server.sel.unregister(self.sock)
        except (KeyError, ValueError):
            pass
        self.sock.close()
        self.sock = None
        self.server.clients.discard(self)


class ControlServer:
    def __init__(self, sel, path, allowed_uids, handler):
        self.sel, self.path, self.allowed, self.handler = sel, path, allowed_uids, handler
        self.clients = set()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        # World-connectable on purpose: the real check is SO_PEERCRED below,
        # so the desktop user needs no group membership (and no re-login).
        os.chmod(path, 0o666)
        self.sock.listen(8)
        self.sock.setblocking(False)
        sel.register(self.sock, selectors.EVENT_READ, lambda mask: self.accept())

    def accept(self):
        try:
            conn, _ = self.sock.accept()
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                log.error("accept failed: %s", e)
            return
        uid = ipc.peer_uid(conn)
        if uid not in self.allowed:
            log.warning("refused control connection from uid %d (not in allowlist)", uid)
            conn.close()
            return
        client = ClientConn(self, conn, uid)
        self.clients.add(client)
        self.sel.register(conn, selectors.EVENT_READ, client.on_event)

    def handle(self, client, msg):
        rid = msg.get("id")
        try:
            return {"id": rid, "ok": True, "result": self.handler(client, msg)}
        except Exception as e:   # bad request must never take the daemon down
            return {"id": rid, "ok": False, "error": str(e)}

    def broadcast(self, topic, payload):
        for client in list(self.clients):
            if topic in client.topics:
                client.send({"event": topic, **payload})

    def wants(self, topic):
        return any(topic in c.topics for c in self.clients)

    def close(self):
        for client in list(self.clients):
            client.close()
        self.sock.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass

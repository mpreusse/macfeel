#!/usr/bin/env python3
"""Record a mouse's raw event stream for replay tests.

    python3 tools/record.py /dev/input/eventN recordings/mx-slow.jsonl

Writes one JSON array per line: [timestamp_s, type, code, value].
Only motion, wheel, mouse buttons and SYN_REPORT are recorded. Keyboard keys
are dropped even on combined keyboard/mouse nodes. Stop with Ctrl+C.
The device must not be grabbed by another program (Toshy, a running macfeeld).
"""

import fcntl
import json
import struct
import sys

import evdev

EVIOCSCLOCKID, CLOCK_MONOTONIC = 0x400445A0, 1
EV_SYN, EV_KEY, EV_REL = 0, 1, 2
BTN_MOUSE_FIRST, BTN_MOUSE_LAST = 0x110, 0x11F


def keep(ev):
    if ev.type == EV_SYN:
        return ev.code == 0
    if ev.type == EV_REL:
        return True
    return ev.type == EV_KEY and BTN_MOUSE_FIRST <= ev.code <= BTN_MOUSE_LAST


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    dev = evdev.InputDevice(sys.argv[1])
    fcntl.ioctl(dev.fd, EVIOCSCLOCKID, struct.pack("i", CLOCK_MONOTONIC))
    print(f"recording {dev.name} -> {sys.argv[2]}; Ctrl+C to stop", file=sys.stderr)
    count = 0
    with open(sys.argv[2], "w") as out:
        try:
            for ev in dev.read_loop():
                if keep(ev):
                    out.write(json.dumps([round(ev.sec + ev.usec / 1e6, 6), ev.type, ev.code, ev.value]) + "\n")
                    count += 1
        except KeyboardInterrupt:
            pass
    print(f"{count} events written", file=sys.stderr)


if __name__ == "__main__":
    main()

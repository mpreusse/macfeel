"""Device discovery and filtering.

`classify()` is a pure function over a plain dict (`DeviceFacts`) so the
selection rules can be unit-tested without hardware. `gather_facts()` builds
that dict from udev + evdev for a real /dev/input/eventN node.
"""

import errno
import os

import pyudev

VIRTUAL_SUFFIX = " (MacFeel)"
VIRTUAL_PHYS = "macfeel"           # phys string we give our own uinput devices

# evdev codes (copied from linux/input-event-codes.h so this module and its
# tests don't need python-evdev just for constants).
EV_KEY, EV_REL = 0x01, 0x02
REL_X, REL_Y = 0x00, 0x01
BTN_LEFT = 0x110
KEY_ENTER = 28
KEY_LETTERS = (16, 17, 18, 19, 20, 21, 22, 23, 24, 25,   # Q..P
               30, 31, 32, 33, 34, 35, 36, 37, 38,       # A..L
               44, 45, 46, 47, 48, 49, 50)               # Z..M
INPUT_PROP_POINTING_STICK = 0x05

# Verdicts
GRAB = "grab"            # we take this device over
SKIP = "skip"            # a mouse-like device we deliberately leave alone (shown in UI)
IGNORE = "ignore"        # not a mouse at all (not shown)


def stable_id(facts):
    """Identifier that survives reboots/replugging, used to persist per-device settings.

    Bluetooth devices have a unique `uniq` (their MAC). For USB, phys encodes
    the port, which is the best we have to tell two identical mice apart.
    """
    where = facts.get("uniq") or facts.get("phys") or ""
    return f"{facts['vendor']:04x}:{facts['product']:04x}:{facts['name']}:{where}"


def is_combined(facts):
    """True if the node can also produce typing keystrokes."""
    keys = facts["keys"]
    return facts["udev"].get("ID_INPUT_KEYBOARD") == "1" or KEY_ENTER in keys or any(
        k in keys for k in KEY_LETTERS)


def classify(facts, device_settings):
    """Decide what to do with one input device.

    Returns (verdict, reason). `device_settings` are the merged per-device
    settings from config (enabled, allow_combined, dpi).
    """
    udev = facts["udev"]
    name = facts["name"]

    # 1. Never touch virtual devices: our own (feedback loop!) or other
    #    remappers' (Toshy/xwaykeyz, keyd, input-remapper, ...). All uinput
    #    devices live under /devices/virtual/input in sysfs; name/phys are
    #    extra checks. (Not all of /devices/virtual: Bluetooth mice come in
    #    through BlueZ's uhid and live under /devices/virtual/misc/uhid.)
    if (name.endswith(VIRTUAL_SUFFIX) or facts.get("phys") == VIRTUAL_PHYS
            or "/devices/virtual/input/" in facts.get("sys_path", "")):
        return IGNORE, "virtual device"

    # 2. Must look like a mouse at all.
    is_pointer = REL_X in facts["rels"] and REL_Y in facts["rels"] and BTN_LEFT in facts["keys"]
    if not is_pointer or udev.get("ID_INPUT_MOUSE") != "1":
        return IGNORE, "not a mouse"

    # 3. Other pointer kinds stay with libinput's own handling.
    for prop, label in (("ID_INPUT_TOUCHPAD", "touchpad"), ("ID_INPUT_TABLET", "tablet"),
                        ("ID_INPUT_TOUCHSCREEN", "touchscreen"), ("ID_INPUT_JOYSTICK", "joystick"),
                        ("ID_INPUT_POINTINGSTICK", "pointing stick")):
        if udev.get(prop) == "1":
            return SKIP, label
    if INPUT_PROP_POINTING_STICK in facts["input_props"]:
        return SKIP, "pointing stick"
    if facts.get("touchpad_sibling"):
        # e.g. "UNIW0001:00 093A:0255 Mouse": the emulated-mouse node of a
        # laptop touchpad. It belongs to the touchpad, not to an external mouse.
        return SKIP, "part of a touchpad"

    # 4. Combined keyboard+mouse nodes. The MX Master over Bluetooth is one of
    #    these (its HID descriptor declares a full keyboard). We grab them by
    #    default and forward key events untouched; the user can opt out.
    if is_combined(facts) and not device_settings.get("allow_combined", True):
        return SKIP, "combined keyboard/mouse device"

    if not device_settings.get("enabled", True):
        return SKIP, "disabled by user"

    return GRAB, "combined keyboard/mouse node" if is_combined(facts) else "mouse"


# ---------------------------------------------------------------------------
# Talking to the real system

def _has_touchpad_sibling(context, udev_dev):
    """Does another input node of the same physical (HID) device report a touchpad?"""
    input_node = udev_dev.parent            # .../inputN
    physical = input_node.parent if input_node is not None else None
    if physical is None:
        return False
    for other in context.list_devices(subsystem="input", parent=physical):
        if other.properties.get("ID_INPUT_TOUCHPAD") == "1":
            return True
    return False


def gather_facts(context, path, evdev_device):
    """Collect everything classify() needs for one /dev/input/eventN."""
    udev_dev = pyudev.Devices.from_device_file(context, path)
    caps = evdev_device.capabilities(absinfo=False)
    info = evdev_device.info
    return {
        "path": path,
        "name": evdev_device.name,
        "phys": evdev_device.phys or "",
        "uniq": evdev_device.uniq or "",
        "vendor": info.vendor,
        "product": info.product,
        "sys_path": udev_dev.sys_path,
        "udev": dict(udev_dev.properties),
        "keys": set(caps.get(EV_KEY, ())),
        "rels": set(caps.get(EV_REL, ())),
        "input_props": set(evdev_device.input_props()),
        "touchpad_sibling": _has_touchpad_sibling(context, udev_dev),
    }


def list_event_nodes():
    return sorted(
        (os.path.join("/dev/input", n) for n in os.listdir("/dev/input") if n.startswith("event")),
        key=lambda p: int(p.rsplit("event", 1)[1]))


# Remappers that grab input devices, recognised by their virtual devices' names.
KNOWN_GRABBERS = {
    "xwaykeyz": "Toshy",
    "keyszer": "Keyszer",
    "xkeysnail": "xkeysnail",
    "input-remapper": "input-remapper",
    "keyd virtual": "keyd",
    "kanata": "kanata",
    "kmonad": "KMonad",
    "makima": "makima",
}


def guess_grabber(sysfs="/sys/class/input"):
    """Which known remapper is running? (The kernel can't tell us who holds a grab.)"""
    try:
        entries = os.listdir(sysfs)
    except OSError:
        return None
    for entry in entries:
        try:
            with open(os.path.join(sysfs, entry, "name")) as f:
                name = f.read().strip().lower()
        except OSError:
            continue
        for marker, program in KNOWN_GRABBERS.items():
            if marker in name:
                return program
    return None


def describe_open_error(err):
    if err.errno in (errno.EACCES, errno.EPERM):
        return "no permission"
    if err.errno == errno.EBUSY:
        return "busy: captured by another program"
    return f"error: {err.strerror}"

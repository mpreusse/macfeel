"""Per-device event processing: grab -> transform -> uinput.

Split in two layers:
  * MotionAccelerator / FrameProcessor: pure logic, (events, timestamp) in,
    events out. Used by replay tests without any device.
  * DevicePipeline: owns the real evdev device and the uinput clone.
"""

import fcntl
import math
import struct

from . import scroll
from .devices import VIRTUAL_PHYS, VIRTUAL_SUFFIX

EV_SYN, EV_KEY, EV_REL, EV_FF, EV_REP = 0x00, 0x01, 0x02, 0x15, 0x14
SYN_REPORT, SYN_DROPPED = 0, 3
REL_X, REL_Y = 0x00, 0x01
REL_HWHEEL, REL_WHEEL = 0x06, 0x08
REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 0x0B, 0x0C
SCROLL_CODES = {REL_WHEEL, REL_HWHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES}

EVIOCSCLOCKID = 0x400445A0  # _IOW('E', 0xa0, int)
CLOCK_MONOTONIC = 1

IDLE_GAP = 0.100            # s; longer gaps are pauses, not report intervals
DEFAULT_INTERVAL = 0.008    # s; assumed report interval until we have measured one


class PointerSettings:
    """What a FrameProcessor needs from the config, resolved for one device."""

    def __init__(self, curve, dpi, report_rate=0, passthrough=False,
                 scroll_enabled=True, vertical_strength=1.0, horizontal_strength=0.0):
        self.curve = curve                    # object with .px_per_count(counts_per_report)
        self.dpi = dpi                        # resolution the curve was built for (for display)
        self.report_rate = report_rate        # 0 = use counts per report as-is (like macOS)
        self.passthrough = passthrough
        self.scroll_enabled = scroll_enabled
        self.vertical_strength = vertical_strength
        self.horizontal_strength = horizontal_strength


class MotionAccelerator:
    """Scales each report's motion vector exactly like macOS does.

    * The input to the curve is the number of counts in this report (the
      vector's length). macOS uses no timestamps and no smoothing, so a mouse
      that reports more often gets smaller per-report counts and less
      acceleration - on the Mac as well as here.
    * Optionally (`report_rate` > 0) the counts are rescaled as if the mouse
      reported at that rate, using the report interval measured from event
      timestamps. Use it if this mouse polls at a different rate on Linux
      than on your Mac.
    * The gain scales the whole vector, so direction is preserved exactly.
    * Fractional output is carried to the next report per axis (sub-pixel
      accumulation), so slow movement is neither lost nor jumpy.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.last_t = None
        self.interval = DEFAULT_INTERVAL  # typical time between reports, measured
        self.rem_x = self.rem_y = 0.0
        self.last_counts = 0.0            # for the live graph
        self.last_gain = 0.0              # pixels per count, for the live graph

    def process(self, dx, dy, t, settings):
        dt = None if self.last_t is None else t - self.last_t
        self.last_t = t
        if dt is not None and 0 < dt <= IDLE_GAP:
            self.interval += 0.05 * (dt - self.interval)

        counts = math.hypot(dx, dy)
        if settings.report_rate:
            counts *= 1.0 / (settings.report_rate * self.interval)
        factor = settings.curve.px_per_count(counts)
        self.last_counts, self.last_gain = counts, factor

        fx = dx * factor + self.rem_x
        fy = dy * factor + self.rem_y
        out_x, out_y = round(fx), round(fy)
        self.rem_x, self.rem_y = fx - out_x, fy - out_y
        return out_x, out_y


class FrameProcessor:
    """Turns the events of one input frame (between SYN_REPORTs) into output events."""

    def __init__(self, rel_caps, settings):
        self.settings = settings
        self.motion = MotionAccelerator()
        self.scroll = {
            "v": scroll.ScrollAxis(REL_WHEEL, REL_WHEEL_HI_RES, REL_WHEEL_HI_RES in rel_caps),
            "h": scroll.ScrollAxis(REL_HWHEEL, REL_HWHEEL_HI_RES, REL_HWHEEL_HI_RES in rel_caps),
        }
        self.was_passthrough = settings.passthrough

    def set_settings(self, settings):
        if settings.passthrough != self.was_passthrough:
            self.reset()
        self.was_passthrough = settings.passthrough
        self.settings = settings

    def reset(self):
        self.motion.reset()
        for axis in self.scroll.values():
            axis.reset()

    def process(self, events, t):
        """`events`: list of (type, code, value) without the SYN_REPORT. Returns same shape."""
        s = self.settings
        if s.passthrough:
            return list(events)

        out, dx, dy, has_motion = [], 0, 0, False
        wheel = {}
        for ev in events:
            etype, code, value = ev
            if etype == EV_REL and code in (REL_X, REL_Y):
                has_motion = True
                if code == REL_X:
                    dx += value
                else:
                    dy += value
            elif etype == EV_REL and code in SCROLL_CODES and s.scroll_enabled:
                wheel[code] = wheel.get(code, 0) + value
            else:
                out.append(ev)   # buttons, MSC_*, keys, raw wheel if scroll accel is off

        if has_motion:
            ox, oy = self.motion.process(dx, dy, t, s)
            if ox:
                out.append((EV_REL, REL_X, ox))
            if oy:
                out.append((EV_REL, REL_Y, oy))

        if wheel:
            for key, strength in (("v", s.vertical_strength), ("h", s.horizontal_strength)):
                out.extend(self.scroll[key].process(wheel, t, strength))
        return out

    def live(self):
        """Last report's counts and gain (pixels per count) for the live graph."""
        m = self.motion
        return {"counts": m.last_counts, "gain": m.last_gain, "t": m.last_t,
                "rate": 1.0 / m.interval}


class DevicePipeline:
    """One grabbed physical mouse and its virtual twin."""

    def __init__(self, path, facts, settings):
        import evdev  # imported here so the pure classes above work without python-evdev

        self.path = path
        self.facts = facts
        self.dev = evdev.InputDevice(path)
        try:
            # Kernel timestamps default to CLOCK_REALTIME, which jumps (NTP,
            # manual changes). Velocity needs a monotonic clock.
            fcntl.ioctl(self.dev.fd, EVIOCSCLOCKID, struct.pack("i", CLOCK_MONOTONIC))

            caps = self.dev.capabilities(absinfo=True)
            for etype in (EV_SYN, EV_FF, EV_REP):
                caps.pop(etype, None)
            info = self.dev.info
            # Decision (spec 4.3): copy bus/vendor/product so libinput applies
            # the same per-model quirks (wheel click angle, hi-res handling)
            # to the twin as it would to the real mouse. DPI quirks don't
            # matter because GNOME runs libinput in "flat" mode, which does
            # not use DPI. The name suffix + phys make the twin recognisable.
            self.ui = evdev.UInput(
                events=caps, name=self.dev.name + VIRTUAL_SUFFIX,
                vendor=info.vendor, product=info.product, version=info.version,
                bustype=info.bustype, phys=VIRTUAL_PHYS, input_props=self.dev.input_props(),
                max_effects=0)  # no force-feedback slots; mice have none
        except Exception:
            self.dev.close()
            raise

        rel_caps = set(caps.get(EV_REL, ()))
        self.frame = FrameProcessor(rel_caps, settings)
        self.pending = []
        self.dropping = False
        try:
            # Create the twin first, grab second: there is never a moment
            # without a working pointer.
            self.dev.grab()
        except Exception:
            self.ui.close()
            self.dev.close()
            raise

    def fileno(self):
        return self.dev.fd

    def set_settings(self, settings):
        self.frame.set_settings(settings)

    def on_readable(self):
        """Read all pending events. Raises OSError if the device is gone."""
        try:
            events = list(self.dev.read())
        except BlockingIOError:
            return
        for ev in events:
            if ev.type == EV_SYN and ev.code == SYN_DROPPED:
                # Kernel buffer overflowed: the current frame is incomplete.
                self.dropping = True
                self.pending.clear()
            elif ev.type == EV_SYN and ev.code == SYN_REPORT:
                if self.dropping:
                    self.dropping = False
                    self.frame.reset()
                else:
                    self._emit(self.frame.process(self.pending, ev.sec + ev.usec / 1e6))
                self.pending.clear()
            elif not self.dropping:
                self.pending.append((ev.type, ev.code, ev.value))

    def _emit(self, out):
        if not out:
            return
        for etype, code, value in out:
            self.ui.write(etype, code, value)
        self.ui.syn()

    def ungrab(self):
        try:
            self.dev.ungrab()
        except OSError:
            pass

    def close(self):
        # Ungrab first so the real device is live again before the twin vanishes.
        self.ungrab()
        for obj in (self.ui, self.dev):
            try:
                obj.close()
            except OSError:
                pass


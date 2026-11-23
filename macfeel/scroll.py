"""Scroll acceleration (pure logic, no I/O): a port of macOS.

Source: Apple's IOHIDFamily, IOHIDEventSystemPlugIns/IOHIDAcceleration.cpp
(IOHIDScrollAccelerator::accelerate) and IOHIDPointerScrollFilter.cpp
(setupScrollAcceleration), tag IOHIDFamily-2238.100.59.

How macOS accelerates a wheel, per report (one axis):
  * Keep the last up to 8 reports (time since the previous one, notches).
    A direction change or a pause > 500 ms starts over.
  * Average the time between reports (each counted as at most 150 ms) and
    the notches per report, then
        v = (A*dt^2 - B*dt + C) * notches          (dt in ms, clamped 1..150)
    and with more than 2 reports in the window, v *= sqrt(count).
  * Look v up in the same defaultAccelTable as the pointer, built with the
    scroll settings (acceleration 0.3125 = macOS default, resolution 9,
    rate 67), and multiply by 0.1.

That gives ~0 for slow, separate notches and rises to ~9.6 for fast
spinning. What macOS then turns into lines/pixels is closed source; the
older open-source kernel path rounded any non-zero value up to one line.
We therefore treat the result as "lines per notch" with a minimum of one:
slow notches scroll 1:1, fast spinning up to ~9.6x. (An assumption; the
calibration page measures it.)

Units here: the kernel's hi-res scroll units, 120 per notch. A mouse with a
hi-res wheel (e.g. MX Master on Linux) reports fractions of a notch, while
macOS sees whole notches; Apple's speed estimate is therefore fed one
"virtual notch" per 120 units of travel, and the resulting multiplier
applies to the fine-grained stream.

Both output streams are derived from the accelerated hi-res value, like the
kernel does: one low-res notch per 120 hi-res units. That keeps smooth-
scrolling apps (GTK, Firefox) and notch-based apps in agreement.
"""

import math

from .curve import DEFAULT_ACCEL_TABLE, AppleTable

EV_REL = 0x02
NOTCH = 120

# IOHIDAcceleration.hpp
HISTORY = 8                          # SCROLL_EVENT_AVARAGE_LENGHT 9 (ring keeps 8)
CLEAR_MS = 500.0                     # SCROLL_CLEAR_THRESHOLD_MS
EVENT_MS = 150.0                     # SCROLL_EVENT_THRESHOLD_MS
A = 0x00000002 / 65536.0             # SCROLL_MULTIPLIER_A
B = 0x000003BB / 65536.0             # SCROLL_MULTIPLIER_B
C = 0x00018041 / 65536.0             # SCROLL_MULTIPLIER_C
PIXEL_TO_WHEEL = 0x0000199A / 65536.0
SCROLL_RESOLUTION = 9.0              # kDefaultScrollFixedResolution (IOHIDEventService.cpp)
FRAME_RATE = 67.0
DEFAULT_ACCELERATION = 0.3125        # EV_DEFAULTSCROLLACCELLEVEL 0x5000


class AppleScrollAccelerator:
    """IOHIDScrollAccelerator for one axis. Input: whole notches per report."""

    def __init__(self, acceleration=DEFAULT_ACCELERATION):
        self.table = AppleTable(acceleration, SCROLL_RESOLUTION, FRAME_RATE, DEFAULT_ACCEL_TABLE)
        self.reset()

    def reset(self):
        self.events = []          # newest last: (delta_ms, |notches|)
        self.last_t = None
        self.direction = None

    def lines(self, notches, t):
        """macOS's (pre-rounding) scroll amount for a report of `notches` at time t (s)."""
        delta_ms = math.inf if self.last_t is None else (t - self.last_t) * 1000.0
        self.last_t = t
        self.events.append((delta_ms, abs(notches)))
        del self.events[:-HISTORY]
        direction = notches > 0
        if direction != self.direction or delta_ms > CLEAR_MS:
            self.events = self.events[-1:]
            self.direction = direction

        sum_dt = sum_scroll = 0.0
        count = 0
        for dt, scroll in reversed(self.events):
            sum_scroll += scroll
            count += 1
            if dt > EVENT_MS:
                sum_dt += EVENT_MS
                break
            sum_dt += dt
            if sum_dt >= CLEAR_MS:
                break

        avg_dt = min(max(sum_dt / count, 1.0), EVENT_MS)
        velocity = (A * avg_dt * avg_dt - B * avg_dt + C) * (sum_scroll / count)
        velocity = max(velocity, 1 / 65536.0)
        if count > 2:
            velocity *= math.sqrt(count * 16) / 4
        return self.table.multiplier(velocity) * PIXEL_TO_WHEEL

    def multiplier(self, notches, t):
        """Scroll multiplier per notch: at least 1 (see module docstring)."""
        return max(1.0, self.lines(notches, t) / abs(notches))


class ScrollAxis:
    """One wheel axis (vertical or horizontal)."""

    def __init__(self, lo_code, hi_code, has_hires):
        self.lo_code, self.hi_code, self.has_hires = lo_code, hi_code, has_hires
        self.apple = None
        self.acceleration = None
        self.reset()

    def reset(self):
        self.mult = 1.0           # current macOS multiplier
        self.travel = 0           # hi-res units since the last virtual notch
        self.direction = 0
        self.last_t = None
        self.hi_rem = 0.0         # fractional hi-res units not emitted yet
        self.lo_acc = 0           # hi-res units not yet turned into a low-res notch
        if self.apple:
            self.apple.reset()

    def process(self, wheel, t, accelerate, acceleration=DEFAULT_ACCELERATION, speed=1.0):
        """`wheel`: {code: value} for this frame. Returns a list of (EV_REL, code, value)."""
        if self.hi_code in wheel:
            units = wheel[self.hi_code]
        elif self.lo_code in wheel:
            units = wheel[self.lo_code] * NOTCH
        else:
            return []
        if units == 0:
            return []

        if accelerate:
            if self.apple is None or acceleration != self.acceleration:
                self.apple = AppleScrollAccelerator(acceleration)
                self.acceleration = acceleration
            direction = 1 if units > 0 else -1
            paused = self.last_t is not None and t - self.last_t > CLEAR_MS / 1000.0
            if direction != self.direction or paused:
                self.travel = 0
                self.mult = 1.0   # a pause or reversal starts a new gesture at 1:1
                self.direction = direction
            self.last_t = t
            self.travel += units
            notches = int(self.travel / NOTCH)        # whole notches completed in this frame
            if notches:
                self.travel -= notches * NOTCH
                self.mult = self.apple.multiplier(notches, t)
            units *= self.mult
        return self.emit(units * speed)

    def emit(self, units):
        self.hi_rem += units
        hi = round(self.hi_rem)
        self.hi_rem -= hi
        if hi == 0:
            return []
        if self.lo_acc and (hi > 0) != (self.lo_acc > 0):
            self.lo_acc = 0       # direction changed: drop the partial notch
        out = []
        if self.has_hires:
            out.append((EV_REL, self.hi_code, hi))
        self.lo_acc += hi
        lo = int(self.lo_acc / NOTCH)          # truncate toward zero, like the kernel
        if lo:
            self.lo_acc -= lo * NOTCH
            out.append((EV_REL, self.lo_code, lo))
        return out

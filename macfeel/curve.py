"""Pointer acceleration curves (pure math, no I/O): a port of macOS.

Source: Apple's IOHIDFamily, https://github.com/apple-oss-distributions/IOHIDFamily
(tags IOHIDFamily-2238.100.59 / 2238.120.5; this math is unchanged since 1633.x).

  IOHIDEventSystemPlugIns/IOHIDAcceleration.cpp
      IOHIDPointerAccelerator::accelerate     - applies a curve to one report
  IOHIDEventSystemPlugIns/IOHIDAccelerationAlgorithm.cpp
      IOHIDTableAcceleration::CreateWithTable - table curves   (default on current macOS)
      IOHIDParametricAcceleration             - parametric curves (if a driver publishes them)
  IOHIDEventSystemPlugIns/IOHIDPointerScrollFilter.{h,cpp}
      defaultAccelTable, FRAME_RATE 67, 400 dpi default resolution, algorithm choice

How macOS accelerates a mouse
-----------------------------
For every HID report (no timing involved, no smoothing):

    v    = floor(length of (dx, dy))      # counts in this report
    mult = curve(v) / v                   # curve(v): pixels this report should move
    dx, dy *= mult                        # same factor on both axes: direction kept

The curve depends on the Tracking-speed slider value and on `resolution`,
the mouse's dpi as macOS understands it: taken from the HID descriptor's
physical units, or 400 when the descriptor has none (most mice, including
the MX Master 3).

Which curve? macOS uses parametric curves only if the mouse's driver
publishes a `HIDAccelCurves` property. The modern mouse driver
(AppleUserHIDEventDriver) does not, so mice get the *table* algorithm built
from `defaultAccelTable`. This is confirmed by measurement: INRIA's
libpointing project drove a real macOS 15.3.2 machine (Apple Silicon) with a
hardware device that sends exact counts and recorded cursor movement. The
data (pointing-echomouse/darwin-24, all 10 slider positions) matches the
table algorithm below exactly; the parametric curves are off by up to 4x.
Older macOS (10.11/10.12, darwin-15/16) matched the parametric curves.
See docs/mac-curve.md.

Units: counts per report in, pixels (macOS points) per report out.
"""

import math
import struct

FRAME_RATE = 67.0              # Apple's assumed report rate
CURSOR_SCALE = 96.0 / 67.0     # kCursorScale
MAC_DEFAULT_DPI = 400          # kDefaultPointerResolutionFixed
DEFAULT_TRACKING = 0.6875      # EV_DEFAULTPOINTERACCELLEVEL 0xb000
MIN_VELOCITY = 1 / 65536.0     # FIXED_TO_DOUBLE(0x1)

# The ten positions of the macOS "Tracking speed" slider. Each one is a
# different curve (not a plain speed multiplier).
MAC_SLIDER_STEPS = (0.0, 0.125, 0.5, 0.6875, 0.875, 1.0, 1.5, 2.0, 2.5, 3.0)


# ---------------------------------------------------------------------------
# Table algorithm (IOHIDTableAcceleration)

# defaultAccelTable from IOHIDPointerScrollFilter.cpp, byte for byte.
# Big-endian: scale (16.16), signature "@200", count (u16), then per curve:
# acceleration (16.16), point count (u16), points (x, y as 16.16).
DEFAULT_ACCEL_TABLE = bytes([
    0x00, 0x00, 0x80, 0x00,
    0x40, 0x32, 0x30, 0x30, 0x00, 0x02, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
    0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00,
    0x00, 0x09, 0x00, 0x00, 0x71, 0x3B, 0x00, 0x00,
    0x60, 0x00, 0x00, 0x04, 0x4E, 0xC5, 0x00, 0x10,
    0x80, 0x00, 0x00, 0x0C, 0x00, 0x00, 0x00, 0x5F,
    0x00, 0x00, 0x00, 0x16, 0xEC, 0x4F, 0x00, 0x8B,
    0x00, 0x00, 0x00, 0x1D, 0x3B, 0x14, 0x00, 0x94,
    0x80, 0x00, 0x00, 0x22, 0x76, 0x27, 0x00, 0x96,
    0x00, 0x00, 0x00, 0x24, 0x62, 0x76, 0x00, 0x96,
    0x00, 0x00, 0x00, 0x26, 0x00, 0x00, 0x00, 0x96,
    0x00, 0x00, 0x00, 0x28, 0x00, 0x00, 0x00, 0x96,
    0x00, 0x00,
])
# Decoded: curve 0.0 = [(1, 1)] (linear); curve 1.0 = [(0.442, 0.375),
# (4.308, 16.5), (12, 95), (22.92, 139), (29.23, 148.5), (34.46, 150),
# (36.38, 150), (38, 150), (40, 150)]: x = device inches/s, y = cursor
# inches/s at 96 dpi (both "per 1/67 s report").


def parse_table(data):
    """-> list of (acceleration, [(x, y), ...]) in table order."""
    _scale, _signature, count = struct.unpack(">IIH", data[:10])
    offset, curves = 10, []
    for _ in range(count):
        accel, n = struct.unpack(">iH", data[offset:offset + 6])
        offset += 6
        points = []
        for _ in range(n):
            x, y = struct.unpack(">ii", data[offset:offset + 8])
            offset += 8
            points.append((x / 65536.0, y / 65536.0))
        curves.append((accel / 65536.0, points))
    return curves


def _interpolate_point(p, p0, p1, ratio):
    # IOHIDTableAcceleration::InterpolatePoint (the 4-argument version)
    m = (p1[1] - p0[1]) / (p1[0] - p0[0])
    b = p1[1] - m * p1[0]
    y = p[0] * m + b
    return p[0], min(p[1], y) + abs(p[1] - y) * ratio


def _interpolate_function(lo, hi, ratio, result):
    # IOHIDTableAcceleration::InterpolateFunction. `result` mimics std::set
    # ordered by x: the first point inserted at a given x wins.
    h, ph0, ph1 = 0, (0.0, 0.0), hi[0]
    for p in lo:
        while p[0] > ph1[0]:
            if h < len(hi) - 1:
                ph0 = ph1
                h += 1
                ph1 = hi[h]
            else:
                break
        result.setdefault(p[0], _interpolate_point(p, ph0, ph1, ratio))


class AppleTable:
    """IOHIDTableAcceleration::CreateWithTable + multiplier, faithfully."""

    def __init__(self, acceleration, resolution=MAC_DEFAULT_DPI, rate=FRAME_RATE,
                 table=DEFAULT_ACCEL_TABLE):
        curves = parse_table(table)
        hi = lo = curves[0]
        for entry in curves:
            lo, hi = hi, entry
            if acceleration <= hi[0]:
                if acceleration == hi[0]:
                    lo = hi
                break

        points = {}
        if hi is lo or acceleration > hi[0]:
            # On a curve exactly, or above the last one: scale its y values.
            scale = (acceleration + 1) / (hi[0] + 1)
            for x, y in hi[1]:
                points.setdefault(x, (x, y * scale))
        else:
            ratio = (acceleration - lo[0]) / (hi[0] - lo[0])
            _interpolate_function(lo[1], hi[1], ratio, points)
            _interpolate_function(hi[1], lo[1], ratio, points)

        # Piecewise-linear segments from (0, 0) through all points, in
        # counts-per-report -> pixels-per-report.
        self.segments = []   # (m, b, x_end)
        p1 = (0.0, 0.0)
        for x in sorted(points):
            p0 = p1
            p1 = (points[x][0] * resolution / rate, points[x][1] * CURSOR_SCALE)
            m = (p1[1] - p0[1]) / (p1[0] - p0[0])
            self.segments.append((m, p1[1] - m * p1[0], p1[0]))

    def multiplier(self, value):
        """Pixels for a report of `value` counts (Apple's name for this function)."""
        curve = self.segments[0]
        for curve in self.segments:
            if not value > curve[2]:
                break
        return curve[0] * value + curve[1]


# ---------------------------------------------------------------------------
# Parametric algorithm (IOHIDParametricAcceleration)

def _fixed(v):
    return v / 65536.0


# HIDAccelCurves from IOHIDFamily/Info-IOHIDEventDriver*.plist (16.16 values):
# (Index, GainLinear, GainParabolic, GainCubic, TangentSpeedLinear, TangentSpeedParabolicRoot)
APPLE_CURVES = [tuple(_fixed(x) for x in row) for row in (
    (0,      65536, 0,      0,     524288, 0),
    (8192,   60293, 26214,  5243,  537395, 1245184),
    (32768,  60948, 36045,  6554,  543949, 1179648),
    (45056,  61604, 46531,  7864,  550502, 1114112),
    (57344,  62259, 57672,  9830,  557056, 1048576),
    (65536,  62915, 69468,  11796, 563610, 983040),
    (98304,  63570, 81920,  14418, 570163, 917504),
    (131072, 64225, 95027,  17695, 576717, 851968),
    (163840, 64881, 108790, 21627, 583270, 786432),
    (196608, 65536, 123208, 26214, 589824, 786432),
)]


class AppleParametric:
    """Speed v (counts per report standardized to inches/s) -> f(v):

        f(v) = g1*v + (g2*v)^2 + (g3*v)^3   for v <= T1  (note: (g2*v)^2, not g2*v^2)
             = m0*v + b0                     for T1 < v <= T2  (tangent line)
             = sqrt(m1*v + b1)               for v > T2  (levels off)

    The tracking value selects/interpolates one of the ten curves above.
    """

    def __init__(self, tracking, resolution=MAC_DEFAULT_DPI, rate=FRAME_RATE):
        self.divider = resolution / rate
        cur = 0
        for k, c in enumerate(APPLE_CURVES):
            if tracking >= c[0]:
                cur = k
        lo = APPLE_CURVES[cur]
        if lo[0] < tracking and cur + 1 < len(APPLE_CURVES):
            hi = APPLE_CURVES[cur + 1]
            r = (tracking - lo[0]) / (hi[0] - lo[0])
            params = [a + r * (b - a) for a, b in zip(lo, hi)]
        else:
            params = list(lo)
        _, self.g1, self.g2, self.g3, tsl, tpr = params

        self.t1 = self.t2 = math.inf
        if tsl:
            self.m0 = self._slope(tsl)
            self.b0 = self._poly(tsl) - self.m0 * tsl
            self.t1 = tsl
            if tpr:
                y1 = self.m0 * tpr + self.b0
                self.m1 = 2 * y1 * self.m0   # sqrt segment has slope m0 at tpr
                self.b1 = y1 * y1 - self.m1 * tpr
                self.t2 = tpr

    def _poly(self, v):
        return self.g1 * v + (self.g2 * v) ** 2 + (self.g3 * v) ** 3

    def _slope(self, v):
        return self.g1 + 2 * self.g2 ** 2 * v + 3 * self.g3 ** 3 * v ** 2

    def f(self, v):
        if v <= self.t1:
            return self._poly(v)
        if v <= self.t2:
            return self.m0 * v + self.b0
        return math.sqrt(self.m1 * v + self.b1)

    def multiplier(self, value):
        """Pixels for a report of `value` counts."""
        return self.f(value / self.divider) * CURSOR_SCALE


# ---------------------------------------------------------------------------
# What the pipeline uses

class MacCurve:
    """Pixels per count for a report of `counts` (vector length) counts.

    tracking : the macOS slider value
    dpi      : resolution macOS assumes for the mouse (400 for most mice)
    algorithm: "table" (current macOS) or "parametric" (macOS 10.11/10.12, or
               mice whose driver publishes HIDAccelCurves)
    strength : 1 = Apple; 0 = no acceleration (Apple's slow-speed gain everywhere).
               Limited to 0..1: going beyond Apple's curve would make the
               pointer move less for faster hands on the curve's flat top.
    speed    : overall multiplier; keeps the curve's shape
    """

    def __init__(self, tracking=DEFAULT_TRACKING, dpi=MAC_DEFAULT_DPI, algorithm="table",
                 strength=1.0, speed=1.0):
        algo_class = AppleParametric if algorithm == "parametric" else AppleTable
        self.algo = algo_class(tracking, dpi)
        self.strength, self.speed = min(max(strength, 0.0), 1.0), speed
        self.base = self._apple(MIN_VELOCITY)

    def _apple(self, counts):
        v = max(math.floor(counts), MIN_VELOCITY)
        return self.algo.multiplier(v) / v

    def px_per_count(self, counts):
        g = self._apple(counts)
        return (self.base + self.strength * (g - self.base)) * self.speed


class LinearCurve:
    """No acceleration: a constant number of pixels per count."""

    def __init__(self, px_per_count):
        self.gain = px_per_count

    def px_per_count(self, counts):
        return self.gain


PRESETS = {
    "mac": "Mac",
    "mac-light": "Mac (gentler)",
    "linear": "Linear (no acceleration)",
}


def from_config(pointer, dpi=MAC_DEFAULT_DPI):
    """Build the active curve from the `pointer` config section for one mouse."""
    preset = pointer.get("preset", "mac")
    tracking = float(pointer.get("tracking_speed", DEFAULT_TRACKING))
    speed = float(pointer.get("speed", 1.0))
    strength = float(pointer.get("acceleration", 1.0))
    algorithm = pointer.get("algorithm", "table")
    if preset == "linear":
        # Same slow-speed precision as the Mac curve, so an A/B switch
        # compares acceleration, not overall speed.
        return LinearCurve(MacCurve(tracking, dpi, algorithm).base * speed)
    if preset == "mac-light":
        strength *= 0.6
    return MacCurve(tracking, dpi, algorithm, strength, speed)

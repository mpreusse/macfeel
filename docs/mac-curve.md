# The Mac pointer curve

How macOS accelerates mouse movement, as far as Apple's published source
shows, and how MacFeel reproduces it. Implementation: `macfeel/curve.py`.

## Sources

Apple's IOHIDFamily, mirrored at
<https://github.com/apple-oss-distributions/IOHIDFamily>, tag
**IOHIDFamily-2238.120.5** (the math is identical in 1633.140.4):

| File | What's in it |
|---|---|
| `IOHIDEventSystemPlugIns/IOHIDAccelerationAlgorithm.cpp` | `IOHIDParametricAcceleration`: the curve and how its segments are joined |
| `IOHIDEventSystemPlugIns/IOHIDAcceleration.cpp` | `IOHIDPointerAccelerator::accelerate`: how the curve is applied per report |
| `IOHIDEventSystemPlugIns/IOHIDPointerScrollFilter.{h,cpp}` | Settings lookup, `FRAME_RATE 67`, 400 dpi default resolution |
| `IOHIDFamily/Info-IOHIDEventDriver*.plist` | Key `HIDAccelCurves`: the ten default curves |
| `IOHIDSystem/IOKit/hidsystem/IOHIPointing.h` | `EV_DEFAULTPOINTERACCELLEVEL 0xb000` = 0.6875 |

The older kernel implementation (`IOHIDSystem/IOHIPointing.cpp`, 16.16
fixed point) does the same math, with integer rounding in the last segment.

## The algorithm

For **every HID report**:

1. **Speed.** Take the length of the movement vector in device counts and
   standardize it:
   `v = counts / (resolution / 67)`.
   With reports at 67 Hz this is hand speed in inches/s. `resolution` comes
   from the HID descriptor's physical units; if there are none, it is
   **400 dpi**. The MX Master 3 declares none, so macOS treats it as 400 dpi.
2. **Curve.** `f(v)` maps speed to cursor speed. There are three pieces,
   joined so that both value and slope are continuous:
   - `v ≤ T1`: `f = g1·v + (g2·v)² + (g3·v)³`, a polynomial. Note:
     `(g2·v)²`, not `g2·v²`; Apple's code comment is misleading.
   - `T1 < v ≤ T2`: `f = m0·v + b0`, the tangent line at T1.
   - `v > T2`: `f = √(m1·v + b1)`, a root that levels off.
3. **Apply.** The cursor moves `f(v)·96/67` pixels along the original
   direction (both axes are scaled by the same factor). In per-second
   terms: cursor speed = 96·f(v) px/s.

The result is S-shaped: slow movements get a low, nearly constant gain for
precision; gain rises steeply through medium speeds; very fast flicks level
off and even fall slightly.

## The Tracking-speed slider

The slider value is **the curve index**. There are ten predefined curves,
and values in between interpolate every coefficient linearly. It is *not*
a speed multiplier: lower values mostly reduce acceleration, while
slow-speed precision barely changes.

| Index (slider) | g1 | g2 | g3 | T1 | T2 |
|---|---|---|---|---|---|
| 0 | 1.00 | – | – | 8.0 | – |
| 0.125 | 0.92 | 0.40 | 0.08 | 8.2 | 19 |
| 0.5 | 0.93 | 0.55 | 0.10 | 8.3 | 18 |
| **0.6875** (default) | 0.94 | 0.71 | 0.12 | 8.4 | 17 |
| 0.875 | 0.95 | 0.88 | 0.15 | 8.5 | 16 |
| 1.0 | 0.96 | 1.06 | 0.18 | 8.6 | 15 |
| 1.5 | 0.97 | 1.25 | 0.22 | 8.7 | 14 |
| 2.0 | 0.98 | 1.45 | 0.27 | 8.8 | 13 |
| 2.5 | 0.99 | 1.66 | 0.33 | 8.9 | 12 |
| 3.0 | 1.00 | 1.88 | 0.40 | 9.0 | 12 |

(Exact values are 16.16 fixed point in the plist; `curve.py` uses those.)

Resulting pixels per count at a few hand speeds (inches/s at 400 dpi):

| Tracking | 0.2 | 2 | 5 | 10 | 20 |
|---|---|---|---|---|---|
| 0.125 (two steps down) | 0.23 | 0.30 | 0.42 | 0.60 | 0.74 |
| 0.5 (one step down) | 0.24 | 0.37 | 0.59 | 0.95 | 1.21 |
| 0.6875 (default) | 0.25 | 0.47 | 0.84 | 1.44 | 1.86 |

## Where MacFeel differs, deliberately

- **Speed from timestamps.** Apple uses counts *per report* and assumes
  67 reports/s, so on a Mac a mouse that polls faster gets less
  acceleration. We measure counts *per second* from kernel timestamps,
  smoothed over ~30 ms. The two match at 67 Hz. Setting
  `pointer.report_rate` to the mouse's report rate on the Mac reproduces
  Apple's per-report behaviour.
- **Overall speed.** `pointer.speed` multiplies the output and keeps the
  curve's shape. macOS has no such control.
- **Acceleration strength.** `pointer.acceleration` blends between no
  acceleration (0) and Apple's curve (1). The default is 1 = Apple.

## Open questions (being verified)

- Does current macOS on Apple Silicon still use this code path and these
  default curves for a Bluetooth mouse, or does another driver supply
  different `HIDAccelCurves`?
- Is anything applied afterwards (WindowServer, display scaling)?
- What is the MX Master's report rate on the Mac?

`tools/mac-probe.sh` answers most of these directly on a real Mac.

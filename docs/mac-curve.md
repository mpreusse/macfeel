# The Mac pointer curve

How macOS accelerates mouse movement, and how MacFeel reproduces it.
Implementation: `macfeel/curve.py` (algorithms) and `macfeel/pipeline.py`
(`MotionAccelerator`, applying it per report).

## Summary

- On current macOS (13 through 26.5 at least), a mouse uses Apple's
  **table** algorithm built from `defaultAccelTable`, applied **per HID
  report** with no timing and no smoothing.
- Speed is the number of counts in one report, interpreted at the
  resolution macOS assumes for the mouse: **400 dpi** unless the HID
  descriptor declares physical units. The MX Master 3 declares none.
- The Tracking-speed slider (`com.apple.mouse.scaling`, 10 ticks from 0 to
  3, default 0.6875) blends between the table's two curves. It changes the
  curve's shape, not just its speed.
- This is verified against measurements of a real Mac running macOS 15.3.2,
  matching within 0.0% at all 10 slider positions (see below).

## Sources

Apple's IOHIDFamily, <https://github.com/apple-oss-distributions/IOHIDFamily>.
We read tags 2238.100.59 and 2238.120.5 (macOS 26.4 / 26.5). The
acceleration files and curves are identical in every tag from 1892.40.8
(macOS 13) on.

| File | What's in it |
|---|---|
| `IOHIDEventSystemPlugIns/IOHIDPointerScrollFilter.cpp` | `defaultAccelTable`, `FRAME_RATE 67`, 400 dpi default, which algorithm is chosen |
| `IOHIDEventSystemPlugIns/IOHIDAcceleration.cpp` | `IOHIDPointerAccelerator::accelerate`: per-report application |
| `IOHIDEventSystemPlugIns/IOHIDAccelerationAlgorithm.cpp` | `IOHIDTableAcceleration` (table) and `IOHIDParametricAcceleration` (parametric) |
| `IOHIDFamily/Info-IOHIDEventDriverSafeBoot.plist` | The ten parametric curves (`HIDAccelCurves`) of the old kernel mouse driver |

**Measurements:** INRIA's libpointing project,
<https://github.com/INRIA/libpointing>, folder `pointing-echomouse/` (commit
46b0e70). A hardware device sends reports of exactly *n* counts to a real
Mac, and the resulting cursor movement is read back. `darwin-24` (macOS
15.3.2, Apple Silicon) matches our table port exactly. `darwin-15/16` (OS X
10.11 / macOS 10.12) match the parametric port. Excerpts are embedded in
`tests/test_curve.py`.

## The algorithm

For every report (`IOHIDPointerAccelerator::accelerate`):

```
v    = floor(sqrt(dx² + dy²))           # counts in this report
mult = curve(v) / v                     # curve: pixels this report should move
dx, dy *= mult                          # one factor for both axes
```

No timestamps are involved, so a mouse that reports more often gets less
acceleration on a Mac. (`HIDPointerReportRate` could change that, but no
mouse driver sets it.)

**Which curve.** `createPointerAlgorithm` uses parametric curves only if
the mouse's driver service carries `HIDAccelCurves`. The modern DriverKit
mouse driver (`AppleUserHIDEventService`) does not, so macOS falls back to
`defaultAccelTable`. The measurements confirm this.

**The table** has two curves of (device inches/s → cursor inches/s at
96 dpi) points:

- acceleration 0.0: `(1, 1)`, i.e. linear
- acceleration 1.0: `(0.44, 0.375) (4.31, 16.5) (12, 95) (22.9, 139) (29.2, 148.5) (34.5, 150) (36.4, 150) (38, 150) (40, 150)`

For a slider value *a*:

- **0 < a < 1:** the two curves are blended with Apple's
  `InterpolateFunction` rule (`y = min(p, line) + |p − line|·a`), applied in
  both directions.
- **a ≥ 1:** the 1.0 curve's y values are multiplied by `(a + 1) / 2`.

The points are then converted to counts per report (`x · resolution/67`)
and pixels per report (`y · 96/67`) and joined into line segments.

Cursor movement per report at 400 dpi (counts → points; measured on macOS
15 = our port):

| Tracking | 5 counts | 10 | 20 | 40 | 80 | 120 |
|---|---|---|---|---|---|---|
| 0.125 (two steps down) | 1.4 | 3.1 | 6.5 | 16.0 | 34.8 | 48.1 |
| 0.5 (one step down) | 2.0 | 5.2 | 11.4 | 34.1 | 81.7 | 106 |
| **0.6875 (default)** | 2.4 | 6.3 | 14.0 | 43.3 | 105 | 135 |
| 1.0 | 2.9 | 8.0 | 18.2 | 58.6 | 144 | 183 |
| 3.0 | 5.9 | 16.1 | 35.8 | 117 | 288 | 366 |

A lower slider value mostly reduces how much fast movements are amplified.
Slow movement (1–3 counts per report) stays nearly the same.

**macOS 14+ "Pointer acceleration" off** (`HIDUseLinearScalingMouseAcceleration`):
output = counts × slider value, with no curve and no resolution term. Not
implemented yet. The `linear` preset is the closest equivalent.

## Scrolling

Same source, `IOHIDScrollAccelerator::accelerate` (`IOHIDAcceleration.cpp`)
and `setupScrollAcceleration` (`IOHIDPointerScrollFilter.cpp`). Ported in
`macfeel/scroll.py`. Per wheel report (one axis, in notches):

1. Keep the last up to 8 reports (time since the previous one, notches). A
   direction change or a pause over 500 ms starts over.
2. Walk back from the newest: sum notches and intervals. An interval over
   150 ms counts as 150 and ends the walk; the walk also ends once the
   intervals reach 500 ms.
3. `v = (A·dt² − B·dt + C) · notches`, with dt = the average interval in ms
   (clamped 1–150) and notches = the average per report.
   A = 2/65536, B = 0x3bb/65536, C = 0x18041/65536. With more than 2
   reports, `v *= √count`.
4. Look `v` up in the same `defaultAccelTable` as the pointer, built with
   the scroll settings (acceleration 0.3125 = `EV_DEFAULTSCROLLACCELLEVEL`,
   resolution 9 = `kDefaultScrollFixedResolution`, rate 67), and multiply
   by 0.1 (`SCROLL_PIXEL_TO_WHEEL_SCALE`).

The result per notch of steady spinning:

| Notches per second | ≤ 7 | 10 | 15 | 20 | 30 | 50 | 100 |
|---|---|---|---|---|---|---|---|
| Apple's value | ~0.002 | 2.0 | 6.0 | 7.1 | 8.3 | 9.0 | 9.4 |
| MacFeel's multiplier | 1× | 2.0× | 6.0× | 7.1× | 8.3× | 9.0× | 9.4× |

**The one assumption.** What macOS turns this value into (lines, pixels)
happens in closed source. The older open-source kernel path rounded any
non-zero value up to one line, which fits everyday experience (a slow notch
always scrolls). MacFeel treats the value as "notches' worth" with a
minimum of 1: slow notches scroll 1:1, fast spinning up to ~9.4× per notch.
How far one notch scrolls is then up to each Linux app, so
`scroll.speed` scales the base distance, and the calibration page measures
both against your Mac.

**Hi-res wheels.** On Linux the MX Master reports 1/8 notches; macOS sees
whole notches. Apple's estimator is fed one "virtual notch" per 120 hi-res
units of travel, and its multiplier applies to the fine-grained stream.
Tests check that both kinds of wheel give the same result.

## Where MacFeel differs, deliberately

- `pointer.speed`: an overall multiplier that keeps the curve's shape.
  macOS has no such control.
- `pointer.acceleration` (0–1): blends between no acceleration and Apple's
  curve. It is capped at 1, because going beyond Apple's curve would make
  the pointer move *less* for faster hands on the curve's flat top.
- `pointer.report_rate` (default 0 = off): rescales counts as if the mouse
  reported at that rate. Use it only if the mouse polls at a different rate
  on Linux than on your Mac. The settings window shows the measured rate.
- `pointer.algorithm = "parametric"`: the older parametric curves, for macOS
  10.11/10.12 or a driver that publishes `HIDAccelCurves`.

## Still open

- Whether your Mac's MX Master service carries `HIDAccelCurves`. That would
  mean the parametric curves; if absent, it's the table. `tools/mac-probe.sh`
  prints `ServiceFilterDebug`, which names the algorithm in use.
- Your slider value (the default is believed to be 0.6875).
- The mouse's report rate on the Mac versus here. The probe prints
  `ReportInterval`; the settings window shows the Linux rate.
- Anything WindowServer does afterwards (closed source; no evidence of
  extra scaling).

# Changelog

## 0.2.0

First public release.

- Pointer acceleration ported from macOS (IOHIDFamily): the table algorithm
  current macOS uses, applied per report, checked against measurements of a
  real Mac (macOS 15) at all 10 Tracking-speed steps. The older parametric
  curves (macOS 10.11–10.12) are selectable.
- Tracking speed with the Mac's 10 steps, plus an Overall speed multiplier
  that keeps the curve's shape.
- Scroll acceleration ported from macOS (IOHIDScrollAccelerator): 1:1 for
  slow notches, up to ~9× per notch when spinning fast. Scroll speed
  setting for the distance per notch; hi-res wheels (e.g. MX Master) handled
  like notch wheels; hi-res and notch events stay consistent.
- Tray icon (no GUI toolkit; works with Ubuntu's AppIndicator extension) and
  a GTK4/libadwaita settings window with a live curve graph.
- Warns when another program (e.g. Toshy) has captured a mouse and shows the
  fix.
- Calibration page to match cursor travel and scrolling against your Mac.
- `macfeel` command: status, pause/resume, settings, calibrate, uninstall.
- Debian package, `install.sh` and `macfeel uninstall`. Sandboxed system
  service; GNOME's own acceleration is switched off while active and
  restored afterwards.

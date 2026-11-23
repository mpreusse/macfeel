# Design decisions

`specs.md` is the original draft. This file records where we deviated from
it and why, and answers the spec's open questions (§15). Newest first within
each section.

## Device handling

### Combined keyboard+mouse nodes are grabbed by default (spec §4.2 said: never)

**Finding.** Over Bluetooth, the MX Master 3 for Mac shows up as a single
evdev node that declares a full keyboard. Its HID report descriptor starts
with a Keyboard collection (report ID 1: modifiers plus key codes 0–164);
the mouse is report ID 2. udev therefore tags it `ID_INPUT_KEYBOARD=1` and
`ID_INPUT_MOUSE=1`. Logitech uses the keyboard part so buttons can send
shortcuts. During normal use it sends no keystrokes.

Under the spec's rule, MacFeel would skip the very mouse it was built for.

**Decision.** Mice whose node also declares keys are grabbed. Their key
events are forwarded unchanged and never logged. The UI marks them
"combined node", and each one can be excluded per device
(`allow_combined: false`). The privacy guarantees are unchanged: there is
no network, movement is never stored, and nothing is logged beyond
lifecycle events.

Through a Logitech USB receiver (Bolt/Unifying), the kernel's
`hid-logitech-dj` driver splits mouse and keyboard into separate nodes, so
this case doesn't arise.

### Toshy must ignore the physical mouse

Toshy (xwaykeyz) grabs every device that has Q–Y plus A–Z plus Space
(`is_keyboard()` in `xwaykeyz/devices.py`), so it grabbed the MX Master.
Grabs are exclusive, so MacFeel got `EBUSY`. Toshy's config has an
`ignore_devices` list that matches names exactly. Add both names:

```python
ignore_devices = [
    'Logitech MX Master 3 for Mac',            # the physical mouse -> MacFeel
    'Logitech MX Master 3 for Mac (MacFeel)',  # our clone -> straight to GNOME
],
```

Cmd+click keeps working: GNOME combines the Ctrl from Toshy's virtual
keyboard with a click from any device. If a device is busy, MacFeel reports
"busy: grabbed by another program" and retries every 5 s.

### Detecting other remappers' virtual devices: `/devices/virtual/input/`, not `/devices/virtual/`

Bluetooth HID devices are created by BlueZ through `uhid`, and their sysfs
path is `/devices/virtual/misc/uhid/…`. uinput devices (ours, Toshy's,
keyd's, …) live under `/devices/virtual/input/`. Only the latter are
excluded, together with our name suffix and `phys=macfeel`.

### Touchpad companion nodes are skipped

This laptop's touchpad also exposes "UNIW0001:00 093A:0255 Mouse", which
udev tags `ID_INPUT_MOUSE=1` (and, notably, `ID_INPUT_POINTINGSTICK=0`, so
properties must be compared with `== "1"`, not merely checked for
presence). Any node whose parent HID device also has a touchpad node is
skipped.

### The virtual clone copies bus/vendor/product (spec §4.3 open question)

The clone is named `"<name> (MacFeel)"`, with `phys=macfeel`, the same
capabilities, and the original bus/vendor/product/version. Copying the IDs
makes libinput apply the same per-model quirks (wheel click angle, hi-res
scroll handling) as it would to the real mouse, which is what we want
because we forward those streams. hwdb `MOUSE_DPI` doesn't matter: GNOME
runs libinput's flat profile, which ignores DPI.

Force-feedback slots are disabled (`max_effects=0`). Without that,
python-evdev would advertise `EV_FF` on a mouse. `EV_REP` (key autorepeat)
is not cloned either.

Checked with `udevadm info`: the clone is classified `ID_INPUT_MOUSE=1`,
`ID_BUS=bluetooth`.

## Acceleration

### Tracking speed means what it means on the Mac; overall speed is separate

In Apple's code, the Tracking-speed slider value *is* the curve index
(`HIDAccelIndex`), interpolated between ten predefined curves. Lowering it
mostly reduces acceleration; slow-speed precision barely changes. It is not
a pure speed multiplier. To reproduce a Mac exactly, copy its
`com.apple.mouse.scaling` value into `tracking_speed`.

Separately, `speed` (default 1.0) scales the output by a constant factor,
so the curve keeps its shape. See [mac-curve.md](mac-curve.md).

### "DPI" is the resolution macOS assumes, default 400 (spec §4.6 said: sensor DPI from hwdb, default 1000)

macOS converts counts to speed using the resolution in the mouse's HID
descriptor (physical min/max), and falls back to 400 dpi when none is
declared. The MX Master 3's descriptor declares none (X/Y are 12-bit
relative values with logical ±2047 only), so macOS treats it as 400 dpi
regardless of its real sensor DPI. Feeling identical to the Mac requires
the same number, so the per-device setting is "resolution as macOS sees
it", default 400. A consequence that matches the Mac: changing the DPI in
Solaar makes the pointer faster or slower.

### Speed comes from timestamps (deliberate difference from Apple)

Apple computes speed per HID report (counts per report × 67 / resolution)
and assumes 67 reports/s. We measure counts per second from kernel
timestamps (CLOCK_MONOTONIC via `EVIOCSCLOCKID`) and smooth them with a
time-based EMA. The two agree for a mouse reporting at 67 Hz. For other
rates, `pointer.report_rate` reproduces Apple's per-report behaviour (set
it to the mouse's report rate on the Mac). Pending: measuring the Mac's
actual rate with `tools/mac-probe.sh`.

## GNOME settings

### Set both `accel-profile=flat` and `speed=0` while active (spec §4.7 said: leave speed alone)

With the flat profile, libinput multiplies all motion by (1 + speed). On
this machine speed was −0.77, presumably to tame GNOME's own acceleration.
That would make the Mac curve run at 0.23×. So the tray backs up both
values, sets `flat` and `0` while active, and restores both on pause or
uninstall. If the user changes GNOME's slider afterwards, we leave it alone.
MacFeel's own `speed` is the intended knob. (Implemented together with the
tray, milestone 4.)

## Pausing

### Pause = pass-through, not ungrab (spec §15 open question)

Pausing keeps the grab and forwards events unchanged. It is instant, and
GNOME doesn't see devices disappear and reappear. Together with the tray
restoring GNOME's acceleration, the mouse then feels like stock Ubuntu.
`kill -9` still releases everything immediately (verified).

## Packaging

### System Python, distributed as a .deb (spec §10 said: install.sh copying to /usr/local)

- **Runtime.** Ubuntu's `python3` plus packaged `python3-evdev`,
  `python3-pyudev` and `python3-gi`. The GTK bindings are only practical
  this way, and they get security updates and rebuilds on Ubuntu upgrades
  (unlike a venv). The code is stdlib plus three libraries, so a Python
  upgrade carries little risk.
- **Package.** `install.sh` builds a .deb with plain `dpkg-deb` and installs
  it with `apt`, which resolves dependencies. dpkg tracks every file, so
  uninstalling leaves nothing behind. The user-session steps (allowlisted
  UID, autostart, GNOME settings) run as the user around the apt call.
- **Rejected:** Flatpak and Snap (sandboxes don't fit a system daemon that
  needs udev rules and uinput) and PyInstaller (bundles its own Python for
  no gain).

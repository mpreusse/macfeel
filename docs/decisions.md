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
keyboard with a click from any device.

### Warn when another program has captured a mouse (instead of editing its config)

Whoever grabs a device first wins. The installed daemon starts at boot,
before Toshy, so it usually wins, but on a Bluetooth reconnect both race.
Without the ignore entry it would therefore *sometimes* fail. MacFeel does
not edit other tools' configs. Instead:

- The daemon reports the mouse as "busy: captured by X". The kernel can't
  say who holds a grab, so X is guessed from known remappers' virtual
  devices (Toshy's "XWayKeyz (virtual) Keyboard", input-remapper, keyd,
  kanata, …), falling back to "another program". It retries every 5 s and
  takes over as soon as the device is free.
- The tray switches to the warning icon, shows a menu line, and sends one
  desktop notification per captured mouse.
- The settings window shows the exact lines to add (with a Copy button)
  and, for Toshy, an "Open Toshy config" button and the
  `toshy-config-restart` hint. The `ignore_devices` list is inside Toshy's
  `keymapper_api` slice, so it survives Toshy upgrades.

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

### Current macOS's table algorithm, applied per report (spec §4.1 said: smoothed velocity from timestamps)

A first port used Apple's *parametric* curves with a timestamp-based,
smoothed velocity. Research then showed that current macOS uses the
*table* algorithm for mice: the modern driver doesn't publish
`HIDAccelCurves`, so macOS falls back to `defaultAccelTable`. It applies
the curve per HID report, with no timing and no smoothing.

INRIA's libpointing measurements of a real macOS 15.3.2 machine match our
table port exactly at all 10 slider positions. The parametric curves are
off by up to 4×; they only match 10.11/10.12. So MacFeel now does what
macOS does: counts per report → table curve. The parametric algorithm
remains selectable (`pointer.algorithm`). `pointer.report_rate` can
normalize a mouse that polls at a different rate on Linux than on the Mac.
Details: [mac-curve.md](mac-curve.md).

### Acceleration strength is capped at 1

Values above 1 extrapolated beyond Apple's curve. On the curve's flat top,
that made the output *decrease* with faster movement (caught by the
monotonicity test). Use `pointer.speed` for "more".

## GNOME settings

### This machine keeps flat / speed 0 permanently (user's choice)

The user prefers GNOME at flat + speed 0 even when MacFeel is paused, so
`~/.config/macfeel/gnome-backup.json` holds those values and "restore" is a
no-op here. The backup/restore mechanism below stays, for everyone else.

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

## User interface

### Tray: our own StatusNotifierItem + dbusmenu over Gio (spec §6.1 said: GTK3 + AppIndicator)

`macfeel/sni.py` publishes the icon (`org.kde.StatusNotifierItem`) and the
menu (`com.canonical.dbusmenu`) directly over D-Bus with `Gio`, in about
200 lines with no toolkit and no extra package. GNOME's AppIndicator
extension, KDE and other panels draw it. Interfaces follow the extension's
own XML in
`/usr/share/gnome-shell/extensions/ubuntu-appindicators@ubuntu.com/interfaces-xml/`.

Ruled out:

- **GTK3 + AyatanaAppIndicator3** works (Toshy uses it), but brings back
  GTK3 and prints a deprecation warning.
- **libayatana-appindicator-glib**, its suggested replacement, exports the
  menu as `org.gtk.Menus`. Ubuntu's AppIndicator extension only reads
  `com.canonical.dbusmenu`, so the icon showed but clicking it did nothing
  (verified by introspecting both sides).

The curve graph draws with GTK4's snapshot/path API (`Gsk.PathBuilder`),
not cairo, so `python3-gi-cairo` isn't needed.

**VS Code note.** VS Code is installed as a snap, and its terminals leak
the snap's own GSettings schemas (`GSETTINGS_SCHEMA_DIR`, `XDG_DATA_DIRS`).
That makes GTK3 abort under Wayland and can confuse other GTK code.
`python3 -m macfeel` strips those variables for the processes it starts.

## Packaging

### Updates: GitHub Releases first, an apt repository later

A `.deb` installed from a file is never updated by apt. For now, re-running
the installer fetches the newest release (`releases/latest/download/macfeel_all.deb`,
a fixed asset name). Once the project is stable, the plan is a signed apt
repository on GitHub Pages, built by GitHub Actions on each release, which
`install.sh` adds so that updates arrive through the normal Ubuntu updater.
A Launchpad PPA is the alternative. There is no in-app update check (privacy).

### systemd hardening: no `PrivateNetwork=yes` (spec §6.3 listed it)

udev's hotplug events reach the daemon over a netlink socket. A private
network namespace never receives them, so hotplug would silently break.
Network access is blocked with `RestrictAddressFamilies=AF_UNIX AF_NETLINK`
and `IPAddressDeny=any` instead. The rest of the spec's list is used, plus
more (`SystemCallFilter=@system-service`, `MemoryDenyWriteExecute`, an empty
capability set, …). The packaged daemon was run under these restrictions
as a transient unit to check that Python, pyudev and evdev work.

### The udev ACL rule covers combined mouse/keyboard nodes

Because combined nodes are grabbed by default, the rule grants the
`macfeel` user access to everything udev tags `ID_INPUT_MOUSE=1`, except
touchpads, tablets, joysticks, pointing sticks and uinput devices, even if
the node also has `ID_INPUT_KEYBOARD=1`. Real keyboards (without the mouse
tag) stay inaccessible.

### Control-socket allowlist: the user who ran `sudo apt install`

The package's postinst records `SUDO_USER`'s (or `PKEXEC_UID`'s) UID in
`/etc/macfeel/allowed-uids`. That avoids a group and the re-login it would
require.

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

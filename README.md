# MacFeel

**Make your mouse on Ubuntu feel like it does on a Mac.**

Coming from macOS, the mouse on Linux feels off: slow, precise movements
are too fast, fast flicks don't go far enough. MacFeel fixes that with a
port of Apple's own pointer-acceleration algorithm, checked against
measurements of a real Mac. Install it with one command, and your mouse
moves the way you're used to. A top-bar icon shows it's working, and a
settings window has the Mac's Tracking-speed slider.

> [!NOTE]
> **This project is completely vibe coded.** All code, tests, packaging and
> docs were written by an AI coding assistant (Claude Code). The author
> directed the work and tested the result, but didn't write the code.
>
> **It is also tested and used every day.** The unit tests compare the
> acceleration against cursor movements measured on a real Mac (macOS 15)
> at all ten Tracking-speed steps, and CI runs them on every change. The
> author uses MacFeel daily on **Ubuntu 26.10 (GNOME 51, Wayland)** with a
> **Logitech MX Master 3 over Bluetooth** and **Toshy**. Other mice,
> distributions and desktops should work but are less tested; issues and
> reports are welcome.
>
> It installs a system service, so read what it does before installing.
> The code is small and readable, and everything is documented in
> [docs/](docs/).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/mpreusse/macfeel/main/install.sh | bash
```

Run it as your normal user; it asks for `sudo` once to install the package
with `apt`. No reboot or re-login is needed. Run the same command again to
update.

**Requirements:** Ubuntu 24.04 or newer (or a Debian-based system with
GTK ≥ 4.14 and libadwaita ≥ 1.4), GNOME on Wayland or X11. Only 26.10 is
tested so far.

**What gets installed:**

- `macfeeld`, a small system service. It runs as its own `macfeel` user
  (not root) with access to input devices, sandboxed by systemd: no network,
  no writable files outside its own settings, no capabilities.
- A tray icon that starts when you log in.
- The **MacFeel** app (settings window).
- A udev rule that lets the `input` group use `/dev/uinput`, needed to create
  the virtual mouse. Toshy's own rule does the same.

**Uninstall:**

```bash
macfeel-uninstall
```

This restores GNOME's mouse settings and removes everything: service, udev
rule, system user and settings.

## Using it

After installing, your mouse moves like on a Mac. The **top-bar icon**
shows the state (active, paused, or a problem) and lets you pause MacFeel.
Paused means stock Ubuntu behaviour.

The **MacFeel** app (also under *Settings…* in the tray menu) has:

- **Tracking speed**: the same 10 steps as the Mac's slider, labelled
  *Mac default*, *1 step slower* and so on. Like on the Mac, a lower step
  mainly reduces how much fast movements are amplified; slow, precise
  movement barely changes.
- **Overall speed**: makes everything faster or slower without changing
  the curve's shape. Use it if your screen is much larger or smaller than
  your Mac's.
- **A live graph** of the acceleration curve, with a dot that follows your
  mouse, and the Mac default as a dashed line for comparison.
- **Scrolling**: Apple's scroll acceleration. Slow, single notches scroll
  one step; spinning the wheel fast scrolls up to ~9× further per notch,
  like on a Mac. *Scroll speed* sets how far one notch goes.
- **Mice**: turn MacFeel on or off per mouse.
- **A test area** to move and scroll in.

While MacFeel is active, GNOME's own mouse acceleration is switched off so
the two don't stack. Your original setting is restored when you pause or
uninstall.

### Matching your Mac exactly

1. **Tracking speed:** on your Mac, run
   `defaults read -g com.apple.mouse.scaling` and pick the same value in
   MacFeel (the default is 0.6875, the fourth step).
2. **Overall speed and Scroll speed:** the Mac and Ubuntu may use different
   screen sizes and scaling, and Linux apps decide themselves how far one
   wheel notch scrolls. Open *Match your Mac* in the settings window. It is
   a page you run in a browser on both machines: move the mouse 10 cm along
   a ruler at three speeds and turn the wheel half a turn slowly and
   quickly, then paste one result into the other. It tells you which
   Overall speed, Tracking speed, Scroll speed and scroll Acceleration to
   use. The page is also at [tools/calibrate.html](tools/calibrate.html).
3. **Mouse DPI:** use the same DPI on both machines. macOS doesn't
   compensate for DPI either, so a higher DPI makes the pointer faster
   there too.

[tools/mac-probe.sh](tools/mac-probe.sh) is a read-only script that prints
the acceleration settings macOS really uses for your mouse, in case you
want to dig deeper.

## Works with Toshy and Solaar

**[Toshy](https://github.com/RedBearAK/toshy)** (Mac keyboard shortcuts)
and MacFeel work together, and Cmd+click keeps working. One catch: some
mice (e.g. the MX Master 3 over Bluetooth) declare a keyboard as well, so
Toshy grabs them too, and only one program can hold a device. MacFeel
detects this, shows a warning, and tells you the two lines to add to
Toshy's `ignore_devices`, with a Copy button and an *Open Toshy config*
button. Afterwards, run `toshy-config-restart`.

**[Solaar](https://github.com/pwr-Solaar/Solaar)** keeps working for DPI,
SmartShift, hi-res scrolling and buttons; it talks to Logitech mice over a
different channel. Changing DPI in Solaar changes pointer speed, just like
on a Mac.

## Privacy and safety

- **No network access, no telemetry, no update checks.** The service is
  blocked from the network by systemd.
- **Only mice are opened.** The service never opens keyboards or virtual
  devices. Key events from mice that declare a keyboard are passed through
  untouched.
- **Nothing about your movements is logged or stored.** The live graph
  data exists only in memory and only goes to the settings window of the
  user who installed MacFeel.
- **Your mouse can't get stuck.** If the service stops for any reason, even
  `kill -9`, the kernel instantly hands the mouse back to GNOME. A watchdog
  releases all mice if the service ever hangs.

## How it works

```
mouse ──▶ macfeeld (applies the Mac curve) ──▶ virtual mouse ──▶ GNOME
```

The service takes exclusive control of each real mouse and creates a
virtual twin named "*\<mouse\> (MacFeel)*". For every movement report, it
does what macOS does: it looks at how many counts the mouse moved in that
report and multiplies the movement by Apple's gain for that speed. Buttons
and everything else pass through unchanged. Because this happens below the
desktop, it works on Wayland and X11.

The curve is a port of Apple's open-source HID code. Current macOS uses a
table-based algorithm, and MacFeel's port reproduces cursor movement
measured on a real Mac running macOS 15. The full story, sources and
numbers are in [docs/mac-curve.md](docs/mac-curve.md).

## FAQ

**Does it work for the touchpad?** No, only for mice. A touchpad is not a
"relative" device like a mouse. It reports finger positions, and libinput
turns them into pointer motion, taps, two-finger scrolling, gestures and
palm rejection. MacFeel works by taking over a device's raw events, so
doing that for a touchpad would mean reimplementing all of that. macOS also
uses a different curve for trackpads. GNOME's own touchpad settings stay in
charge.

**Gaming mice with high polling rates?** They work, but feel slower,
exactly as they would on a Mac: macOS computes speed per report, so a mouse
that reports 1000 times per second gets less acceleration. *Advanced →
Normalize report rate* compensates.

**Other desktops (KDE, …)?** The service doesn't depend on the desktop.
The tray icon uses the standard StatusNotifierItem protocol. Switching off
the desktop's own acceleration is only automated for GNOME; elsewhere, set
the mouse acceleration profile to "flat" yourself.

**The tray says "no mouse found".** Check `journalctl -u macfeeld -n 30`.
If your mouse is listed as "captured by …", another program holds it (see
Toshy above).

## Development

See [docs/development.md](docs/development.md): running from the source
tree, tests, building the package, releasing. Design decisions and how
they differ from the original plan are in
[docs/decisions.md](docs/decisions.md).

## Credits

- The acceleration algorithm and its default table are ported from Apple's
  [IOHIDFamily](https://github.com/apple-oss-distributions/IOHIDFamily)
  (Apple Public Source License 2.0). MacFeel is not affiliated with or
  endorsed by Apple.
- The measurements of real Macs used to verify the port (and embedded in the
  tests) come from INRIA's
  [libpointing](https://github.com/INRIA/libpointing) project (EchoMouse).
- Insights from [Mac Mouse Fix](https://github.com/noah-nuebling/mac-mouse-fix)
  and [LinearMouse](https://github.com/linearmouse/linearmouse) pointed
  the research in the right direction.

## License

[MIT](LICENSE)

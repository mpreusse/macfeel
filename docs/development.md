# Development

## Layout

```
mouse ──evdev (grabbed)──▶ macfeeld ──uinput──▶ "<name> (MacFeel)" ──▶ libinput / GNOME (flat profile)
                              │ Mac curve on motion, scroll acceleration on wheels,
                              │ everything else (buttons, keys) passed through unchanged
                              └─ control socket (/run/macfeel/control.sock) ◀──▶ tray / settings
```

| Path | Purpose |
|---|---|
| `macfeel/curve.py` | Apple's table and parametric algorithms (pure math) |
| `macfeel/scroll.py` | Scroll acceleration, hi-res/low-res consistent (pure) |
| `macfeel/pipeline.py` | Per-report processing (pure) and the device/uinput wrapper |
| `macfeel/devices.py` | Which devices to grab (pure rules), facts from udev, grabber detection |
| `macfeel/daemon.py` | Main loop: discovery, hotplug, config reload, watchdog |
| `macfeel/control.py`, `ipc.py` | Control socket: newline-delimited JSON, `SO_PEERCRED` allowlist |
| `macfeel/config.py` | Defaults in code; only overrides are stored |
| `macfeel/tray.py`, `sni.py` | Tray icon and menu, published over D-Bus (StatusNotifierItem + dbusmenu) |
| `macfeel/settings.py` | Settings window (GTK4 + libadwaita) |
| `macfeel/gnome.py`, `uilink.py` | GNOME mouse settings; UI connection to the daemon |
| `macfeel/cli.py` | The `macfeel` command (status, pause/resume, settings, …) |
| `macfeel/__main__.py`, `macfeel.sh` | One-command launcher for development |
| `tools/calibrate.html` | Calibration page (also installed to `/usr/share/macfeel/`) |
| `tools/mac-probe.sh` | Read-only: dump macOS's live acceleration settings |
| `tools/record.py` | Record a mouse's raw events for replay tests |
| `tools/build-deb.sh`, `packaging/` | Build `dist/macfeel_<version>_all.deb` |
| `data/` | systemd unit, udev rule, desktop entries, icons |
| `install.sh`, `uninstall.sh` | Install / remove (uninstall is installed as `macfeel-uninstall`, also run by `macfeel uninstall`) |
| `Makefile`, `tools/release.sh` | `make test`, `make deb`, `make install`, `make release`, … (`make` lists them) |

Background reading: [mac-curve.md](mac-curve.md) (the algorithm and its
sources), [decisions.md](decisions.md) (every deviation from the original
[specs.md](specs.md) and why).

## Running from the source tree

If MacFeel is installed, stop the service first: `sudo systemctl stop macfeeld`.

```bash
sudo apt install python3-evdev python3-pyudev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
./macfeel.sh                   # daemon + tray + settings window; Ctrl+C stops all
./macfeel.sh --no-settings
```

You need to be in the `input` group (read access to `/dev/input/event*`,
and `/dev/uinput` must be accessible to the group, which `data/70-macfeel.rules`
or Toshy's own rule arrange). Settings are stored in `macfeel-dev.json` in
the project folder. The parts also run separately: `python3 -m
macfeel.daemon`, `macfeel.tray`, `macfeel.settings`.

`./macfeel.sh` strips the environment variables a snap (e.g. VS Code) leaks
into its terminals. Started by hand from such a terminal, GTK can abort.

## Settings reference

The UI writes these through the daemon; they end up in
`/var/lib/macfeel/config.json` (installed) or `macfeel-dev.json` (source tree).

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | `false` = pass everything through unchanged |
| `pointer.preset` | `"mac"` | `"mac"`, `"mac-light"` (60 % acceleration), `"linear"` (no acceleration, same slow-speed precision) |
| `pointer.tracking_speed` | `0.6875` | The macOS slider: `0, 0.125, 0.5, 0.6875, 0.875, 1, 1.5, 2, 2.5, 3`. Each step is a different Apple curve |
| `pointer.speed` | `1.0` | Overall multiplier; keeps the curve's shape |
| `pointer.acceleration` | `1.0` | 0 = no acceleration … 1 = Apple's curve |
| `pointer.algorithm` | `"table"` | `"table"` = current macOS; `"parametric"` = macOS 10.11/10.12 |
| `pointer.report_rate` | `0` | 0 = per report, like macOS; otherwise normalize to this rate (Hz) |
| `scroll.enabled` | `true` | Mac scroll acceleration; `false` = wheel events unchanged |
| `scroll.acceleration` | `0.3125` | macOS's scroll acceleration value (its default); 0 = none |
| `scroll.speed` | `1.0` | Distance per notch |
| `scroll.horizontal` | `false` | Also accelerate the horizontal / thumb wheel |
| `devices.<id>.enabled` | `true` | Per mouse |
| `devices.<id>.dpi` | `400` | Resolution macOS assumes for the mouse (400 unless the mouse declares one). Lower = faster |
| `devices.<id>.allow_combined` | `true` | Grab nodes that also declare keyboard keys (e.g. MX Master over Bluetooth) |

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

They cover the curve (including measured macOS data from INRIA's
libpointing project), scroll, the per-report pipeline, device rules, config
and grabber detection. CI runs them on every push. To record a real event
stream for replay tests (motion, wheel and buttons only, never keys):

```bash
python3 tools/record.py /dev/input/eventN recordings/mx-slow.jsonl
```

## Building and releasing

```bash
make deb                      # -> dist/macfeel_<version>_all.deb and dist/macfeel_all.deb
make install                  # build + install from the checkout
```

To publish a release:

1. While working, add entries under `## Unreleased` in `CHANGELOG.md`.
2. Run `make release` (patch bump) or `make release BUMP=minor` / `BUMP=major`.
   It checks that you're on a clean `main` in sync with GitHub, that the
   changelog has entries and that the tests pass, shows what it will do and
   asks. Then it bumps `macfeel/__init__.py`, turns `## Unreleased` into the
   version heading, commits, tags `vX.Y.Z` and pushes `main` and the tag.
3. The *Release* workflow checks that the tag matches the version, runs the
   tests, builds the `.deb` and creates the GitHub release with both
   `macfeel_<version>_all.deb` and `macfeel_all.deb`, using the changelog
   section as release notes. The installer one-liner downloads the latter
   from the latest release.

First-time setup of the repository:

```bash
git remote add origin https://github.com/mpreusse/macfeel.git
git push -u origin main
```

## Manual test checklist

- Plug/unplug a USB receiver; Bluetooth off/on; switch the mouse between hosts
- Suspend/resume
- Two mice at once
- `sudo kill -9 $(pgrep -f macfeel.daemon)`: the mouse must work normally at once
- Pause in the tray (stock behaviour), resume (Mac feel)
- Scrolling in Firefox, Chrome, Nautilus, Text Editor, VS Code, a Flutter app
- With Toshy: Cmd+click a link in Firefox opens a new tab
- `macfeel-uninstall`: GNOME settings restored, no files, users or rules left

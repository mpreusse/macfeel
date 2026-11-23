# MacFeel

Makes an external mouse on Ubuntu (GNOME, Wayland or X11) move like it does
on macOS, using a port of Apple's own pointer-acceleration algorithm.

**Status:** early development, installable.

## Install

```bash
./install.sh            # from this checkout: builds the .deb and installs it
```
Once published on GitHub, the same works without a checkout:
`curl -fsSL https://raw.githubusercontent.com/OWNER/macfeel/main/install.sh | bash`.

This installs a system service (`macfeeld`, running sandboxed as its own
`macfeel` user with the `input` group; it only ever opens mice, see
[decisions.md](docs/decisions.md#device-access-via-the-input-group-not-per-user-acls-spec-62-said-acls-only)),
a tray icon that starts at login, and the "MacFeel" settings app. No reboot or re-login is needed.
Re-run it to update.

**Uninstall:** `macfeel-uninstall`. It restores GNOME's mouse settings and
removes everything (service, udev rule, system user, config).

If you use Toshy, see
[decisions.md](docs/decisions.md#toshy-must-ignore-the-physical-mouse); the
settings window shows the exact lines to add.

- [docs/decisions.md](docs/decisions.md): where and why we deviate from the
  original [specs.md](specs.md); answers to its open questions.
- [docs/mac-curve.md](docs/mac-curve.md): Apple's algorithm, sources and
  constants.

## How it works

```
mouse ──evdev (grabbed)──▶ macfeeld ──uinput──▶ "<name> (MacFeel)" ──▶ libinput / GNOME (flat profile)
                              │ Mac curve on motion, scroll acceleration on wheels,
                              │ everything else (buttons, keys) passed through unchanged
                              └─ control socket ◀──▶ tray / settings (coming)
```

If the daemon stops for any reason, including `kill -9`, the kernel
releases the grab and the mouse works normally again immediately.

| Module | Purpose |
|---|---|
| `macfeel/curve.py` | Apple's table and parametric algorithms (pure math) |
| `macfeel/scroll.py` | Scroll acceleration, hi-res/low-res consistent (pure) |
| `macfeel/pipeline.py` | Per-frame processing (pure) and the device/uinput wrapper |
| `macfeel/devices.py` | Which devices to grab (pure rules) and gathering facts from udev |
| `macfeel/daemon.py` | Main loop: discovery, hotplug, config reload, watchdog |
| `macfeel/control.py`, `ipc.py` | Control socket (server and client) |
| `macfeel/tray.py`, `sni.py` | Tray icon and menu, published over D-Bus (no toolkit) |
| `macfeel/settings.py` | Settings window (GTK4 + libadwaita) |
| `macfeel/__main__.py`, `macfeel.sh` | One-command launcher for development |
| `macfeel/gnome.py`, `uilink.py` | GNOME mouse settings handling; UI connection to the daemon |
| `tools/record.py` | Record a mouse's raw events for replay tests |
| `tools/mac-probe.sh` | Read-only: dump macOS's live acceleration settings |
| `tools/build-deb.sh`, `packaging/` | Build `dist/macfeel_<version>_all.deb` |
| `install.sh`, `uninstall.sh` | Install / remove (uninstall is also installed as `macfeel-uninstall`) |
| `data/` | systemd unit, udev rule, desktop entries, icons |

## Running it from the source tree (development)

If MacFeel is installed, stop the service first: `sudo systemctl stop macfeeld`.

Requirements:
```bash
sudo apt install python3-evdev python3-pyudev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```
plus membership in the `input` group (for read access to
`/dev/input/event*` and `/dev/uinput`).

1. **If you use Toshy,** make it ignore your mouse; see
   [decisions.md](docs/decisions.md#toshy-must-ignore-the-physical-mouse).
   For the MX Master 3 over Bluetooth, add to `ignore_devices` in
   `~/.config/toshy/toshy_config.py`:
   `'Logitech MX Master 3 for Mac'` and
   `'Logitech MX Master 3 for Mac (MacFeel)'`. Then restart Toshy.
2. **Switch GNOME's own acceleration off,** so it doesn't stack on top. Note
   your current values first so you can restore them; the tray will automate
   this later.
   ```bash
   gsettings get org.gnome.desktop.peripherals.mouse accel-profile   # note it
   gsettings get org.gnome.desktop.peripherals.mouse speed           # note it
   gsettings set org.gnome.desktop.peripherals.mouse accel-profile flat
   gsettings set org.gnome.desktop.peripherals.mouse speed 0
   ```
3. **Start everything with one command:**
   ```bash
   ./macfeel.sh                   # daemon + tray + settings window; Ctrl+C stops all
   ./macfeel.sh --no-settings     # without opening the settings window
   ```
   This works from any terminal, including VS Code's. Settings are stored in
   `macfeel-dev.json` in the project folder (only values that differ from
   the defaults). The parts can also run separately: `python3 -m
   macfeel.daemon`, `macfeel.tray`, `macfeel.settings`.

## Settings

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | `false` = pass everything through unchanged |
| `pointer.preset` | `"mac"` | `"mac"`, `"mac-light"` (60 % acceleration), `"linear"` (no acceleration, same slow-speed precision; for A/B tests) |
| `pointer.tracking_speed` | `0.6875` | The macOS slider. Steps: `0, 0.125, 0.5, 0.6875, 0.875, 1, 1.5, 2, 2.5, 3`. Each step is a different Apple curve: lower steps accelerate less, while slow movement stays nearly the same. One step down from the default is `0.5`. Copy your Mac's `defaults read -g com.apple.mouse.scaling` for an exact match. |
| `pointer.speed` | `1.0` | Overall multiplier; keeps the curve's shape |
| `pointer.acceleration` | `1.0` | 0 = no acceleration … 1 = Apple's curve |
| `pointer.algorithm` | `"table"` | `"table"` = current macOS (measured); `"parametric"` = macOS 10.11/10.12 |
| `pointer.report_rate` | `0` | 0 = per report, exactly like macOS. Otherwise normalize to this rate (Hz), if the mouse polls differently here than on your Mac |
| `scroll.enabled` | `true` | Scroll acceleration on/off |
| `scroll.vertical_strength` | `1.0` | 0 = 1:1, 1 = Mac-like (curve is provisional) |
| `scroll.horizontal_strength` | `0.0` | Thumb wheel; pass-through by default |
| `devices.<id>.enabled` | `true` | Per device |
| `devices.<id>.dpi` | `400` | The resolution macOS assumes for this mouse (400 when the mouse doesn't declare one, which is the MX Master's case). Lower = faster. |
| `devices.<id>.allow_combined` | `true` | Grab nodes that also declare keyboard keys (MX Master over Bluetooth) |

Device ids are `vendor:product:name:uniq-or-phys`; the daemon will show them
in the UI (coming).

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

Unit tests cover the curve (including Apple's per-report numbers), scroll,
pipeline (sub-pixel accumulation, direction, timestamps, idle gaps), device
rules and config. To record a real event stream for replay tests (motion,
wheel and buttons only; key events are never recorded):

```bash
python3 tools/record.py /dev/input/eventN recordings/mx-slow.jsonl
```

## Matching your Mac exactly

On the Mac, with the same mouse connected, run `bash tools/mac-probe.sh >
mac-probe.txt`. It is read-only and prints the slider value, the curves
macOS uses, the resolution it assumes for the mouse and its report
interval. Those numbers go straight into the settings above. A calibration
page for side-by-side measurement comes in milestone 7.

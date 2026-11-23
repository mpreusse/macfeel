# MacFeel — Mac-like mouse acceleration for Ubuntu

*Working name; rename freely.*

## 1. Goal

A small, trustworthy app that makes any external mouse on Ubuntu feel like it does on macOS. The user installs it with one command, never touches GNOME's mouse settings, sees a menu-bar icon confirming it's working, and can uninstall it with one command that restores everything exactly as it was.

### User story

> I came from macOS. I install MacFeel, and my mouse immediately moves and scrolls the way I'm used to. A tray icon shows it's active. If I want, I can open a settings window, see the acceleration curve, and tweak it. If I pause or uninstall it, my mouse goes back to normal instantly.

### Non-goals (v1)

- Touchpads and trackpoints (explicitly excluded; see §4.2)
- Button remapping, gestures, per-app profiles
- Gaming mice at very high polling rates (≥ 4000 Hz) — should work, but not optimized for
- Distros other than Ubuntu/Debian; desktops other than GNOME (nice if they work, not tested)

## 2. Target environment

- Ubuntu 26.10, GNOME 51, **Wayland** session (primary). X11 should also work since the approach sits below the compositor.
- System Python 3 as shipped by Ubuntu. **No pip, no venv, no cargo** — only Ubuntu-packaged dependencies (§9).
- Must coexist with **Toshy** (keyboard remapper, also evdev/uinput based) and **Solaar** (Logitech HID++ tool).
- Test hardware: Logitech MX Master 3 (Bolt/Unifying receiver and Bluetooth). Must also work with any generic USB/Bluetooth mouse.

## 3. How it works (overview)

```
physical mouse ──evdev (grabbed)──▶ MacFeel daemon ──uinput──▶ virtual mouse ──▶ libinput / GNOME
                                        │  applies Mac curve to motion + scroll
                                        │  passes buttons and everything else through unchanged
                                        └─ Unix socket ◀──▶ tray icon + settings window (user session)
```

1. The daemon finds every real mouse, grabs it exclusively (`EVIOCGRAB`) so GNOME no longer sees its raw events, and creates one matching virtual mouse per physical device via uinput.
2. Motion events are run through a Mac-like acceleration curve; scroll events through a Mac-like scroll curve; everything else is passed through unchanged.
3. GNOME's own pointer acceleration is set to **flat** so it doesn't stack on top. GNOME's speed slider is left alone and keeps working as an overall faster/slower multiplier.

## 4. Functional requirements

### 4.1 Pointer acceleration

- Process input per `SYN_REPORT` frame: combine `REL_X`/`REL_Y` of a frame into one vector.
- Compute velocity from **event timestamps** (not wall-clock time), in physical units (e.g. inches/s or mm/s), using the device's DPI (§4.6).
- Apply a smoothed velocity (e.g. exponential moving average over a short window, ~tens of ms) to avoid jitter.
- Gain = f(velocity) from the active curve (§5). Output = input × gain.
- **Sub-pixel accumulation is mandatory**: keep fractional remainders per axis and carry them into the next frame, so slow movements are neither lost nor jumpy.
- Direction must be preserved exactly (scale the vector, don't treat axes independently for gain).
- Handle idle gaps: if dt is large (e.g. > 100 ms), treat the frame as the start of a new movement rather than as extremely slow motion.

### 4.2 Device selection — "works with every mouse"

Grab a device only if **all** of these hold:

- It advertises `REL_X` and `REL_Y` and at least `BTN_LEFT`.
- udev marks it `ID_INPUT_MOUSE=1`.
- It is **not** a touchpad (`ID_INPUT_TOUCHPAD`), tablet, touchscreen, joystick, or pointing stick (`INPUT_PROP_POINTING_STICK`).
- It does **not** advertise keyboard keys (e.g. any of `KEY_A`…`KEY_Z`, `KEY_ENTER`). Some receivers expose combined keyboard+mouse nodes; MacFeel must **never** grab a device that can produce keystrokes (privacy + Toshy conflict). Skip such devices and report them in the UI as "skipped: combined keyboard/mouse device".
- It is not a virtual device created by MacFeel itself (check name suffix **and** physical path) or by another remapper (e.g. Toshy's/xwaykeyz's virtual devices). Avoid feedback loops.

Additional:

- Hotplug: detect devices appearing/disappearing (USB, Bluetooth reconnect, receiver re-pairing, suspend/resume) via `pyudev` monitoring. Never crash on device removal.
- Per-device enable/disable (persisted by a stable identifier: vendor, product, name, and phys/uniq where available).

### 4.3 Virtual device

- One virtual device per physical device, cloning its capabilities (`python-evdev`'s `UInput.from_device` or equivalent), named `"<original name> (MacFeel)"`.
- Must be classified by libinput/GNOME as a normal mouse.
- **Verify**: whether copying the original vendor/product IDs causes libinput quirks/hwdb entries (e.g. `MOUSE_DPI`) to be applied to the virtual device, and whether that's desirable. Document the decision.

### 4.4 Scroll acceleration (toggle, default on)

- Handle `REL_WHEEL` / `REL_HWHEEL` and the high-resolution variants `REL_WHEEL_HI_RES` / `REL_HWHEEL_HI_RES` (120 units = one notch).
- Mac-like behavior: slow scrolling ≈ 1:1; fast consecutive notches/flicks get progressively multiplied.
- Keep hi-res and low-res streams **consistent** (as the kernel does): accumulate hi-res units and emit one low-res notch per 120 hi-res units, so both smooth-scrolling apps (Firefox, GTK) and notch-based apps (Electron/VS Code, Flutter apps) behave correctly.
- Separate settings for vertical and horizontal (MX Master thumb wheel). Horizontal default: pass-through or gentle.

### 4.5 Pass-through

All other events (all buttons including extra/side buttons, `MSC_*`, etc.) are forwarded unchanged with minimal latency.

### 4.6 DPI handling

- Default DPI from udev hwdb property `MOUSE_DPI` if present (use the default/starred value), otherwise 1000.
- User-overridable per device in the settings window (e.g. "Mouse DPI: 1000 — set this to match Solaar/your mouse").

### 4.7 GNOME settings handling

- On enable: back up `org.gnome.desktop.peripherals.mouse accel-profile` (and `speed`, for reference only) to `~/.config/macfeel/gnome-backup.json` (only if no backup exists yet), then set `accel-profile` to `flat`.
- On pause/disable and on uninstall: restore the backed-up `accel-profile`, so the mouse feels like stock Ubuntu again immediately.
- **Do not change `speed`.** It remains the user's overall multiplier.
- `gsettings` must be called in the user's session, so this is the **tray app's** job, not the system daemon's.
- If the user changes `accel-profile` back while MacFeel is active, show a warning in the tray/settings ("GNOME acceleration is on — curves are stacking") with a one-click fix.

## 5. The Mac curve

This is the hard part. Approach in order:

1. **Research**: Apple publishes the source of its HID drivers (IOHIDFamily on opensource.apple.com), which reportedly contains the pointer and scroll acceleration implementation (acceleration tables and/or parametric curves). Find it, understand the algorithm and the default parameters for the standard "Tracking speed" setting, and port the relevant math. Cite the exact files/versions used in a comment.
2. If the source isn't usable, implement a parametric S-shaped gain curve (low, nearly constant gain at slow speed; smooth ramp; plateau at high speed) with parameters that can be tuned to match.
3. **Calibrate empirically** (§10, milestone 7): the user has a Mac and the same MX Master 3, so fitting against real measurements is the final authority.

Implementation requirements:

- The curve is a **pure function** (`velocity → gain`) in its own module, unit-tested, with no device or I/O dependencies.
- Presets: `mac` (default), `mac-light` (gentler), `linear` (no acceleration — useful for A/B testing).
- Expose a small number of user-facing parameters (e.g. "Tracking speed" mirroring the macOS slider, plus "Acceleration strength"), not raw curve coefficients. Advanced parameters may live in an "Advanced" expander.

## 6. Architecture

### 6.1 Components

| Component | Runs as | Responsibility |
|---|---|---|
| `macfeeld` (daemon) | systemd **system** service, dedicated system user `macfeel` | Device discovery, grabbing, curve, uinput output, IPC server |
| `macfeel-tray` | user session (autostart) | Tray icon + menu, GNOME `accel-profile` handling, launches settings |
| `macfeel-settings` | user session (launched on demand) | Settings window with live curve graph |

The tray (GTK3 + AppIndicator) and the settings window (GTK4 + libadwaita) are **separate processes**, because GTK3 and GTK4 can't be mixed in one process.

### 6.2 Permissions (least privilege)

- The daemon runs as system user `macfeel` (no login shell, no home), **not root**.
- Grant `macfeel` access **only to mouse event nodes** and `/dev/uinput`, via a udev rule using ACLs (e.g. `setfacl -m u:macfeel:rw $devnode`) matching `ID_INPUT_MOUSE=="1"` and `ID_INPUT_KEYBOARD!="1"`. Do **not** add `macfeel` (or the desktop user) to the `input` group — that would expose keyboards.
- Verify the matching with `udevadm info` on the real devices and document the final rule.
- Do not change ownership/mode of device nodes (other tools, e.g. Toshy, rely on defaults).

### 6.3 systemd hardening (daemon)

Use as much as works, verify each:

```
User=macfeel
PrivateNetwork=yes
RestrictAddressFamilies=AF_UNIX
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
DevicePolicy=closed
DeviceAllow=char-input rw
DeviceAllow=/dev/uinput rw
StateDirectory=macfeel
RuntimeDirectory=macfeel
Restart=on-failure
RestartSec=2
```

(`DeviceAllow=char-input` allows the input char-device class; actual access is still limited by the ACLs.)

### 6.4 IPC

- Unix socket at `/run/macfeel/control.sock`, newline-delimited JSON.
- Access control: socket mode permissive enough for the desktop user, **and** the daemon checks the peer's UID via `SO_PEERCRED` against an allowlist recorded at install time (avoids needing a group + re-login).
- Messages (suggested): `status`, `devices`, `set_enabled`, `set_device_enabled`, `get_config`, `set_config`, `subscribe_live` (streams current smoothed velocity + gain at ~20–30 Hz for the live graph; nothing is stored).

### 6.5 Config

- Owned and written by the daemon at `/var/lib/macfeel/config.json` (atomic write: temp file + rename). JSON to avoid extra dependencies.
- The UI never writes the file directly; it sends `set_config`.
- Changes apply live, no restart.
- Defaults live in code; the file only stores overrides.

## 7. User interface

### 7.1 Tray icon (AppIndicator; Ubuntu ships the AppIndicator extension enabled)

- Icon states: **active**, **paused**, **error** (daemon unreachable or no devices). Simple monochrome symbolic icons that fit the GNOME top bar in light and dark.
- Menu:
  - Status line (non-clickable), e.g. "Active — 1 mouse" / "Paused" / "Daemon not running"
  - Enable/Pause toggle (pausing restores GNOME acceleration and makes the daemon pass events through unchanged — or ungrab; pick whichever switches instantly and reliably)
  - Devices submenu: each detected mouse with a checkbox; skipped devices shown greyed with the reason
  - "Settings…"
  - "Quit tray icon" (daemon keeps running)

### 7.2 Settings window (GTK4 + libadwaita, follows system light/dark)

- Master enable switch
- Preset selector (Mac / Mac light / Linear)
- "Tracking speed" slider (macOS-style) and "Acceleration" slider
- **Live curve graph**: gain vs. speed, with a dot that moves in real time as the user moves the mouse — this is the "I can see it's working" feature
- Scrolling: enable toggle, strength slider, horizontal setting
- Devices list: per-device toggle, DPI field, status
- Notice explaining that GNOME's own speed slider still works as an overall multiplier
- A small "test area" to move the pointer and scroll in

## 8. Safety and privacy

**Safety**

- If the daemon crashes or is killed, the kernel releases the grab when the fd closes, so the physical mouse instantly works normally again. Verify with `kill -9`.
- Internal watchdog: if the event loop stalls beyond a threshold (e.g. 500 ms), ungrab all devices and log an error.
- Keyboards are never touched, so the user can always stop it from the keyboard (`sudo systemctl stop macfeeld`).
- `Restart=on-failure` with a start limit so a crash loop can't keep the mouse in a broken state.

**Privacy**

- No network access (enforced by `PrivateNetwork=yes`). No telemetry, no update checks.
- Only mouse devices are ever opened (enforced by device filtering **and** udev ACLs).
- Never log or persist movement data. Journal logs only lifecycle events: start/stop, device added/removed/skipped, errors.
- Live velocity data exists only in memory and is sent only over the local socket to allowlisted UIDs.

## 9. Dependencies (Ubuntu packages only)

- `python3`, `python3-evdev`, `python3-pyudev`, `python3-gi`
- `gir1.2-gtk-3.0`, `gir1.2-ayatanaappindicator3-0.1` (tray)
- `gir1.2-gtk-4.0`, `gir1.2-adw-1` (settings)
- `acl` (`setfacl`, for udev rule)

Verify exact package names on Ubuntu 26.10. No other third-party code.

## 10. Install / uninstall

**`install.sh`** — run as the normal user; uses `sudo` only for system steps and says what it's doing:

1. `apt install` the dependencies.
2. Create system user `macfeel`.
3. Install code to `/usr/local/lib/macfeel/`, launchers to `/usr/local/bin/`.
4. Install udev rule to `/etc/udev/rules.d/70-macfeel.rules`; `udevadm control --reload` and `udevadm trigger` for input devices (aim: **no reboot or re-login required**).
5. Install and `enable --now` the systemd unit.
6. Record the installing user's UID in the daemon's allowlist.
7. Install tray autostart entry (`~/.config/autostart/`), `.desktop` entry for Settings, and icons.
8. Start the tray, which backs up and flips GNOME's `accel-profile` to flat.

Must be idempotent (safe to re-run for updates).

**`uninstall.sh`**:

1. Restore GNOME `accel-profile` from backup (as the user).
2. Stop/disable/remove the service, udev rule (reload + trigger), installed files, autostart, desktop entry, icons.
3. Remove the `macfeel` system user and `/var/lib/macfeel`.
4. Leave nothing behind except, optionally, the user's config backup if they choose to keep it.

## 11. Coexistence checks

- **Toshy**: Cmd+click (Toshy turns it into Ctrl+click) must still work, e.g. Cmd+click a link in Firefox opens a new tab. MacFeel must not grab Toshy's virtual devices, and Toshy must keep working on keyboards.
- **Solaar**: still able to change DPI, SmartShift, hi-res scrolling (it talks HID++ via hidraw, not evdev). After a DPI change in Solaar, the user updates MacFeel's DPI field (or MacFeel reads it if easily possible — optional).

## 12. Suggested project layout

```
macfeel/
├── macfeel/
│   ├── curve.py          # pure curve math (unit-tested)
│   ├── scroll.py         # pure scroll math (unit-tested)
│   ├── devices.py        # discovery, filtering, hotplug
│   ├── pipeline.py       # per-device event loop: grab → transform → uinput
│   ├── daemon.py         # main, IPC server, config, watchdog
│   ├── ipc.py            # socket protocol (shared by daemon and UI)
│   ├── config.py
│   ├── tray.py           # GTK3 + AppIndicator
│   └── settings.py       # GTK4 + libadwaita
├── data/
│   ├── macfeeld.service
│   ├── 70-macfeel.rules
│   ├── macfeel-tray.desktop
│   ├── macfeel-settings.desktop
│   └── icons/
├── tools/
│   └── calibrate.html    # see milestone 7
├── tests/
├── install.sh
├── uninstall.sh
└── README.md
```

## 13. Milestones

1. **Pass-through daemon (run manually from a terminal)**: discover the MX Master, grab it, create the virtual device, forward everything unchanged. Success = zero noticeable difference; `Ctrl+C` / `kill -9` instantly restores the mouse. Don't install anything system-wide yet.
2. **Curve**: add pointer acceleration with the `mac` and `linear` presets, sub-pixel accumulation, smoothing. Config file + live reload.
3. **System integration**: system user, udev ACL rule, hardened systemd unit, `install.sh` / `uninstall.sh`, hotplug, suspend/resume.
4. **IPC + tray icon**, including GNOME `accel-profile` backup/flip/restore.
5. **Settings window** with live curve graph and device list.
6. **Scroll acceleration** (vertical + horizontal, hi-res consistent).
7. **Calibration tool**: a single self-contained HTML page (works in any browser on both macOS and Ubuntu) that records pointer movement while the user moves the mouse a fixed physical distance (e.g. 10 cm along a ruler) at slow, medium and fast speeds, and reports cursor travel in pixels and average speed. Run it on the Mac and on Ubuntu with the same mouse and display scaling noted; adjust curve parameters until the numbers match. Document the procedure in the README.

## 14. Testing

- **Unit tests** for `curve.py` and `scroll.py` (monotonicity, continuity, no NaN/inf, sub-pixel accumulation over many tiny moves sums correctly, direction preservation).
- **Replay tests**: record raw event streams (timestamps + values) from the real mouse once, replay through the pipeline without devices, assert outputs.
- **Manual checklist**:
  - Plug/unplug USB receiver; Bluetooth disconnect/reconnect; switch MX Master between hosts and back
  - Suspend/resume
  - Two mice at once
  - `kill -9` the daemon → mouse works normally immediately
  - Pause from tray → feels like stock Ubuntu; resume → Mac feel
  - Scrolling in Firefox, Chrome, Nautilus, GNOME Text Editor, VS Code, a Flutter app (Ubuntu App Center)
  - Toshy Cmd+click still works
  - Uninstall → GNOME setting restored, no files/users/rules left behind

## 15. Notes for the builder (Claude Code)

- Before any step that uses `sudo` or changes system state, explain what it does and ask the user to confirm.
- Keep milestones 1–2 runnable manually from a terminal before installing anything system-wide.
- Keep the code small and readable: the user wants to be able to read all of it. Prefer clarity over cleverness; comment the curve math.
- Open questions to resolve and document: exact Apple algorithm source and parameters; whether to clone vendor/product IDs on the virtual device; whether pausing should ungrab or pass through; exact udev rule that matches mice but never keyboards on this machine.
#!/bin/bash
# MacFeel installer: Mac-like mouse acceleration for Ubuntu (GNOME).
#
#   From GitHub:      curl -fsSL https://raw.githubusercontent.com/mpreusse/macfeel/main/install.sh | bash
#   From a checkout:  ./install.sh          (builds the package from this source tree)
#
# Run as your normal user. sudo is used once, to install the package with apt.
# Safe to re-run: it updates to the newest version.
set -euo pipefail

REPO="${MACFEEL_REPO:-mpreusse/macfeel}"     # GitHub "owner/name" to download releases from

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m==>\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m==> %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run this as your normal user (not root). It asks for sudo when needed."
command -v apt-get >/dev/null || die "This installer needs an apt-based system (Ubuntu, Debian)."

# A development instance (./macfeel.sh) would hold the mouse and block the service.
if pgrep -u "$(id -u)" -f "python3 -m macfeel.daemon" >/dev/null; then
    die "A development copy of MacFeel is running (./macfeel.sh). Stop it first (Ctrl+C)."
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# 1. Get the package: build it from this checkout, or download the latest release.
SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/tools/build-deb.sh" ]; then
    SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fi
if [ -n "$SRC" ]; then
    say "Building the package from $SRC"
    DEB="$SRC/$("$SRC/tools/build-deb.sh")"
else
    say "Downloading the latest MacFeel release from github.com/$REPO"
    DEB="$TMP/macfeel_all.deb"
    curl -fsSL -o "$DEB" "https://github.com/$REPO/releases/latest/download/macfeel_all.deb" \
        || die "Download failed. Is github.com/$REPO correct and does it have a release?"
fi

# 2. Install it. apt resolves the dependencies; the package then creates the
#    "macfeel" system user, installs the udev rule and starts the service.
say "Installing (sudo): system service, udev rule for mice, tray autostart, settings app"
sudo apt-get install -y --reinstall "$DEB"

# 3. Start the tray now (it also starts automatically at every login). It
#    switches GNOME's own mouse acceleration to "flat" while MacFeel is active.
pkill -u "$(id -u)" -f "macfeel.tray" 2>/dev/null || true
setsid -f macfeel-tray >/dev/null 2>&1 < /dev/null

# 4. Toshy grabs mice that also declare keyboard keys (e.g. MX Master over Bluetooth).
if grep -qi "xwaykeyz" /sys/class/input/*/name 2>/dev/null; then
    warn "Toshy is running. If MacFeel shows your mouse as \"captured by Toshy\", add the mouse"
    warn "to ignore_devices in ~/.config/toshy/toshy_config.py (the settings window shows the"
    warn "exact lines), then run: toshy-config-restart"
fi

sleep 1
if systemctl is-active --quiet macfeeld; then
    say "Done. MacFeel is active: look for the mouse icon in the top bar,"
    say "or open \"MacFeel\" from your apps. Status: macfeel   Uninstall: macfeel uninstall"
else
    warn "The service did not start. See: journalctl -u macfeeld -n 50"
    exit 1
fi

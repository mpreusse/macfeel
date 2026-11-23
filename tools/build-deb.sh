#!/bin/bash
# Build dist/macfeel_<version>_all.deb from the source tree (no root needed).
#     tools/build-deb.sh
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
VERSION=$(python3 -c 'import macfeel; print(macfeel.__version__)')
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

install -d "$STAGE/usr/lib/macfeel/macfeel"
install -m 644 macfeel/*.py "$STAGE/usr/lib/macfeel/macfeel/"
install -D -m 755 -t "$STAGE/usr/bin" packaging/bin/macfeel packaging/bin/macfeeld packaging/bin/macfeel-tray packaging/bin/macfeel-settings packaging/bin/macfeel-uninstall
install -D -m 644 data/macfeeld.service "$STAGE/usr/lib/systemd/system/macfeeld.service"
install -D -m 644 data/70-macfeel.rules "$STAGE/usr/lib/udev/rules.d/70-macfeel.rules"
install -D -m 644 data/macfeel-tray.desktop "$STAGE/etc/xdg/autostart/macfeel-tray.desktop"
install -D -m 644 data/io.github.macfeel.Settings.desktop "$STAGE/usr/share/applications/io.github.macfeel.Settings.desktop"
install -D -m 644 data/icons/io.github.macfeel.Settings.svg "$STAGE/usr/share/icons/hicolor/scalable/apps/io.github.macfeel.Settings.svg"
install -d "$STAGE/usr/share/macfeel/icons"
install -m 644 data/icons/macfeel-*-symbolic.svg "$STAGE/usr/share/macfeel/icons/"
install -m 644 tools/calibrate.html "$STAGE/usr/share/macfeel/calibrate.html"
install -d "$STAGE/usr/share/doc/macfeel"
install -m 644 README.md docs/*.md "$STAGE/usr/share/doc/macfeel/"

install -d "$STAGE/DEBIAN"
sed "s/@VERSION@/$VERSION/" packaging/debian/control.in > "$STAGE/DEBIAN/control"
echo "Installed-Size: $(du -sk "$STAGE" | cut -f1)" >> "$STAGE/DEBIAN/control"
install -m 755 packaging/debian/postinst packaging/debian/prerm packaging/debian/postrm "$STAGE/DEBIAN/"
echo "/etc/xdg/autostart/macfeel-tray.desktop" > "$STAGE/DEBIAN/conffiles"

mkdir -p dist
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "dist/macfeel_${VERSION}_all.deb" >/dev/null
cp "dist/macfeel_${VERSION}_all.deb" dist/macfeel_all.deb   # fixed name for "latest release" downloads
echo "dist/macfeel_${VERSION}_all.deb"

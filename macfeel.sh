#!/bin/sh
# Start MacFeel from the source tree: daemon + tray + settings. Ctrl+C stops all.
cd "$(dirname "$(readlink -f "$0")")" && exec python3 -m macfeel "$@"

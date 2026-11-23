"""Start everything with one command (development, before installing):

    python3 -m macfeel            # daemon + tray + settings window
    python3 -m macfeel --no-settings

Ctrl+C stops all of it and gives the mouse back. Once MacFeel is installed,
the daemon runs as a system service and the tray starts at login instead.
"""

import argparse
import os
import signal
import subprocess
import sys
import time

from . import ipc

PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def session_env():
    """The environment for GUI processes, minus anything a snap (e.g. VS Code)
    leaked into it: snap GSettings schemas make GTK abort under Wayland."""
    env = dict(os.environ)
    for key in ("GSETTINGS_SCHEMA_DIR", "GDK_PIXBUF_MODULE_FILE", "GTK_PATH", "GTK_EXE_PREFIX",
                "GIO_MODULE_DIR", "LOCPATH"):
        if "/snap/" in env.get(key, ""):
            del env[key]
    for key in ("XDG_DATA_DIRS", "XDG_CONFIG_DIRS"):
        if key in env:
            parts = [p for p in env[key].split(":") if "/snap/" not in p or p == "/var/lib/snapd/desktop"]
            env[key] = ":".join(parts)
    if os.environ.get("XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG"):
        env["XDG_CONFIG_DIRS"] = os.environ["XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG"]
    return env


def daemon_running():
    try:
        ipc.Client(timeout=0.5).close()
        return True
    except ConnectionError:
        return False


def main():
    parser = argparse.ArgumentParser(description="Run MacFeel (daemon, tray, settings)")
    parser.add_argument("--no-settings", action="store_true", help="don't open the settings window")
    args = parser.parse_args()

    env = session_env()
    core, extra = [], []      # stay alive while a core process runs; extras may come and go

    def start(module, group):
        group.append(subprocess.Popen([sys.executable, "-m", module], cwd=PACKAGE_PARENT, env=env))

    if daemon_running():
        print("macfeel: daemon already running; using it")
    else:
        start("macfeel.daemon", core)
        for _ in range(30):          # wait until its control socket answers
            time.sleep(0.1)
            if daemon_running():
                break
    start("macfeel.tray", core)
    if not args.no_settings:
        start("macfeel.settings", extra)

    print("macfeel: running. Ctrl+C stops everything.")
    signal.signal(signal.SIGTERM, signal.default_int_handler)   # treat like Ctrl+C
    try:
        while any(p.poll() is None for p in core):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        everything = core + extra
        for p in everything:
            if p.poll() is None:
                p.terminate()   # SIGTERM: GTK apps ignore SIGINT
        for p in everything:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        print("macfeel: stopped")

if __name__ == "__main__":
    main()

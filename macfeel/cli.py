"""The `macfeel` command: status and quick actions from a terminal."""

import argparse
import os
import shutil
import subprocess
import sys

from . import __version__, ipc
from .uilink import calibration_page, package_parent


def client():
    try:
        return ipc.Client()
    except ConnectionError:
        sys.exit("MacFeel's service is not running. Start it with: sudo systemctl start macfeeld")


def cmd_status(_args):
    c = client()
    try:
        st = c.request("status")
        conf = c.request("get_config")["config"]
    except RuntimeError as e:      # e.g. not in the allowlist
        sys.exit(f"macfeel: {e}")
    finally:
        c.close()
    p, s = conf["pointer"], conf["scroll"]
    state = "active" if st["enabled"] else "paused"
    version = st.get("version", "older than 0.2.0")
    print(f"MacFeel {version}: {state}, {st['grabbed']} mouse(s) handled")
    for dev in st["devices"]:
        mark = "●" if dev["grabbed"] else "○"
        print(f"  {mark} {dev['name']}: {dev['reason']}")
    if not st["devices"]:
        print("  (no mouse found)")
    print(f"Pointer: tracking speed {p['tracking_speed']:g}, overall speed {p['speed'] * 100:.0f} %, "
          f"curve {p['preset']} ({p.get('algorithm', 'table')})")
    print(f"Scroll:  {'Mac acceleration' if s['enabled'] else 'unchanged'}, "
          f"speed {s.get('speed', 1.0) * 100:.0f} %")


def cmd_enable(enabled):
    def run(_args):
        c = client()
        try:
            c.request("set_enabled", enabled=enabled)
        finally:
            c.close()
        print("MacFeel " + ("resumed: Mac feel on." if enabled else "paused: stock mouse behaviour."))
    return run


def launch(module):
    env = dict(os.environ)
    env["PYTHONPATH"] = package_parent() + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.Popen([sys.executable, "-m", module], env=env, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_settings(_args):
    launch("macfeel.settings")


def cmd_calibrate(_args):
    page = calibration_page()
    if not page:
        sys.exit("macfeel: calibration page not found")
    subprocess.Popen(["xdg-open", page], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Opened {page}")


def cmd_uninstall(_args):
    tool = shutil.which("macfeel-uninstall")
    if not tool:
        sys.exit("macfeel: macfeel-uninstall not found (is MacFeel installed as a package?)")
    os.execv(tool, [tool])


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="macfeel", description="Mac-like mouse acceleration for Ubuntu. Without a command: status.")
    parser.add_argument("-V", "--version", action="version", version=f"MacFeel {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")
    for name, func, text in (
        ("status", cmd_status, "show the service state, mice and settings"),
        ("pause", cmd_enable(False), "switch the Mac feel off (stock behaviour)"),
        ("resume", cmd_enable(True), "switch the Mac feel back on"),
        ("settings", cmd_settings, "open the settings window"),
        ("calibrate", cmd_calibrate, "open the calibration page in your browser"),
        ("uninstall", cmd_uninstall, "remove MacFeel completely"),
        ("version", lambda _a: print(f"MacFeel {__version__}"), "print the version"),
    ):
        sub.add_parser(name, help=text).set_defaults(func=func)
    args = parser.parse_args(argv)
    (getattr(args, "func", None) or cmd_status)(args)


if __name__ == "__main__":
    main()

"""Configuration: defaults in code, overrides in a JSON file.

The file only stores values that differ from the defaults, so improving a
default in a later version reaches users who never touched that setting.
The daemon owns the file; UIs change it via IPC (`set_config`).
"""

import copy
import json
import os
import tempfile

DEFAULTS = {
    "enabled": True,
    "pointer": {
        "preset": "mac",          # "mac" | "mac-light" | "linear"
        "tracking_speed": 0.6875, # the macOS slider value (com.apple.mouse.scaling); selects the curve
        "speed": 1.0,             # overall multiplier; scales the cursor without changing the curve shape
        "acceleration": 1.0,      # 0 = none ... 1 = Apple's curve
        "algorithm": "table",     # "table" (current macOS) or "parametric" (macOS 10.11/10.12)
        "report_rate": 0,         # 0 = per report like macOS; >0 = normalize to this rate (Hz)
    },
    "scroll": {
        "enabled": True,          # Mac scroll acceleration; False = wheel events unchanged
        "acceleration": 0.3125,   # macOS scroll acceleration value (its default)
        "speed": 1.0,             # distance per notch (1 = like an unaccelerated notch)
        "horizontal": False,      # also accelerate the horizontal / thumb wheel
    },
    # Per-device overrides keyed by stable device id (see devices.stable_id):
    #   {"enabled": bool, "dpi": int, "allow_combined": bool}
    "devices": {},
}

# allow_combined: grab mice whose node also declares keyboard keys (e.g. MX Master
# over Bluetooth). Their key events are forwarded untouched, never logged.
DEVICE_DEFAULTS = {"enabled": True, "dpi": None, "allow_combined": True}


def merge(base, overrides):
    """Return a deep copy of `base` with `overrides` applied (dicts merge, everything else replaces)."""
    result = copy.deepcopy(base)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def diff(full, base):
    """Inverse of merge: the minimal overrides that turn `base` into `full`."""
    out = {}
    for key, value in full.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            sub = diff(value, base[key])
            if sub:
                out[key] = sub
        elif key not in base or base[key] != value:
            out[key] = copy.deepcopy(value)
    return out


def device_settings(config, device_id):
    return merge(DEVICE_DEFAULTS, config["devices"].get(device_id, {}))


class ConfigStore:
    def __init__(self, path):
        self.path = path
        self.overrides = {}
        self.mtime = None
        self.load()

    @property
    def config(self):
        return merge(DEFAULTS, self.overrides)

    def load(self):
        """(Re)read the file. A missing or broken file means "all defaults"."""
        try:
            with open(self.path) as f:
                data = json.load(f)
            self.overrides = data if isinstance(data, dict) else {}
            self.mtime = os.stat(self.path).st_mtime_ns
        except FileNotFoundError:
            self.overrides, self.mtime = {}, None
        except (OSError, ValueError) as e:
            print(f"config: cannot read {self.path}: {e}; using defaults", flush=True)
            self.overrides, self.mtime = {}, None

    def changed_on_disk(self):
        try:
            return os.stat(self.path).st_mtime_ns != self.mtime
        except FileNotFoundError:
            return self.mtime is not None

    def update(self, changes):
        """Apply a partial config (same shape as DEFAULTS) and persist it atomically."""
        full = merge(self.config, changes)
        self.overrides = diff(full, DEFAULTS)
        self.save()

    def save(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.overrides, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)  # atomic: readers see old or new, never half
        except BaseException:
            os.unlink(tmp)
            raise
        self.mtime = os.stat(self.path).st_mtime_ns

import json
import os
import tempfile
import unittest

from macfeel import config as c


class ConfigTest(unittest.TestCase):
    def test_merge_and_diff_roundtrip(self):
        full = c.merge(c.DEFAULTS, {"pointer": {"tracking_speed": 2.0}, "devices": {"x": {"dpi": 800}}})
        self.assertEqual(full["pointer"]["preset"], "mac")
        self.assertEqual(full["pointer"]["tracking_speed"], 2.0)
        self.assertEqual(c.diff(full, c.DEFAULTS),
                         {"pointer": {"tracking_speed": 2.0}, "devices": {"x": {"dpi": 800}}})

    def test_store_persists_only_overrides_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "config.json")
            store = c.ConfigStore(path)
            self.assertEqual(store.config, c.DEFAULTS)
            store.update({"scroll": {"enabled": False}})
            with open(path) as f:
                self.assertEqual(json.load(f), {"scroll": {"enabled": False}})
            self.assertEqual(os.listdir(os.path.dirname(path)), ["config.json"])  # no temp leftovers
            self.assertFalse(store.changed_on_disk())
            store.update({"scroll": {"enabled": True}})   # back to default -> nothing stored
            self.assertEqual(c.ConfigStore(path).overrides, {})

    def test_broken_file_means_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not json")
        try:
            self.assertEqual(c.ConfigStore(f.name).config, c.DEFAULTS)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()

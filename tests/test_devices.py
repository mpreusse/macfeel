import unittest

from macfeel import devices as d

MOUSE_KEYS = {d.BTN_LEFT, 0x111, 0x112, 0x113, 0x114}


def facts(**over):
    f = {"name": "Generic Mouse", "phys": "usb-0000:00:14.0-2/input0", "uniq": "",
         "vendor": 0x046d, "product": 0xc077, "sys_path": "/sys/devices/pci0000:00/x/input/input9/event9",
         "udev": {"ID_INPUT_MOUSE": "1"}, "keys": set(MOUSE_KEYS), "rels": {0, 1, 8},
         "input_props": set(), "touchpad_sibling": False}
    f.update(over)
    return f


ON = {"enabled": True, "allow_combined": True}


class ClassifyTest(unittest.TestCase):
    def test_plain_mouse_grabbed(self):
        self.assertEqual(d.classify(facts(), ON)[0], d.GRAB)

    def test_own_and_foreign_virtual_devices_ignored(self):
        self.assertEqual(d.classify(facts(name="Foo (MacFeel)"), ON)[0], d.IGNORE)
        self.assertEqual(d.classify(facts(phys="macfeel"), ON)[0], d.IGNORE)
        toshy = facts(name="XWayKeyz (virtual) Keyboard", phys="py-evdev-uinput",
                      sys_path="/sys/devices/virtual/input/input30/event14")
        self.assertEqual(d.classify(toshy, ON)[0], d.IGNORE)

    def test_bluetooth_mouse_via_uhid_is_not_virtual(self):
        bt = facts(name="Logitech MX Master 3 for Mac", phys="bc:38:98:a8:cb:b2",
                   sys_path="/sys/devices/virtual/misc/uhid/0005:046D:B023.0002/input/input18/event13")
        self.assertEqual(d.classify(bt, ON)[0], d.GRAB)

    def test_not_a_mouse(self):
        self.assertEqual(d.classify(facts(udev={}), ON)[0], d.IGNORE)
        self.assertEqual(d.classify(facts(rels={8}), ON)[0], d.IGNORE)
        self.assertEqual(d.classify(facts(keys=set()), ON)[0], d.IGNORE)

    def test_touchpads_and_friends_skipped(self):
        self.assertEqual(d.classify(facts(udev={"ID_INPUT_MOUSE": "1", "ID_INPUT_TOUCHPAD": "1"}), ON)[0], d.SKIP)
        self.assertEqual(d.classify(facts(input_props={d.INPUT_PROP_POINTING_STICK}), ON)[0], d.SKIP)
        self.assertEqual(d.classify(facts(touchpad_sibling=True), ON)[0], d.SKIP)
        # ID_INPUT_POINTINGSTICK=0 (seen on this laptop's touchpad mouse node) is not "1"
        self.assertEqual(d.classify(facts(udev={"ID_INPUT_MOUSE": "1", "ID_INPUT_POINTINGSTICK": "0"}), ON)[0], d.GRAB)

    def test_combined_device_policy(self):
        mx = facts(name="Logitech MX Master 3 for Mac", keys=MOUSE_KEYS | set(d.KEY_LETTERS),
                   udev={"ID_INPUT_MOUSE": "1", "ID_INPUT_KEYBOARD": "1"})
        self.assertTrue(d.is_combined(mx))
        self.assertEqual(d.classify(mx, ON)[0], d.GRAB)
        verdict, reason = d.classify(mx, {"enabled": True, "allow_combined": False})
        self.assertEqual((verdict, reason), (d.SKIP, "combined keyboard/mouse device"))

    def test_disabled_by_user(self):
        self.assertEqual(d.classify(facts(), {"enabled": False}), (d.SKIP, "disabled by user"))

    def test_stable_id_prefers_uniq(self):
        self.assertEqual(d.stable_id(facts(uniq="aa:bb")), "046d:c077:Generic Mouse:aa:bb")
        self.assertTrue(d.stable_id(facts()).endswith("usb-0000:00:14.0-2/input0"))


if __name__ == "__main__":
    unittest.main()

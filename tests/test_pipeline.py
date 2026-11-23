import math
import unittest

from macfeel import curve
from macfeel.pipeline import (EV_KEY, EV_REL, REL_WHEEL, REL_WHEEL_HI_RES, REL_X, REL_Y,
                              FrameProcessor, MotionAccelerator, PointerSettings)


def settings(**kw):
    kw.setdefault("curve", curve.from_config({"preset": "mac"}))
    kw.setdefault("dpi", 400)
    return PointerSettings(**kw)


class MotionTest(unittest.TestCase):
    def test_subpixel_accumulation_sums_correctly(self):
        # 2000 one-count reports: each alone rounds to 0 px, the total must not be lost.
        s = settings(curve=curve.LinearCurve(0.24))
        acc = MotionAccelerator()
        total = sum(acc.process(1, 0, i * 0.008, s)[0] for i in range(2000))
        self.assertLessEqual(abs(total - 2000 * 0.24), 1)

    def test_direction_preserved(self):
        s = settings()
        acc = MotionAccelerator()
        sx = sy = 0
        for i in range(200):
            x, y = acc.process(30, 17, i * 0.008, s)
            sx += x
            sy += y
        self.assertAlmostEqual(math.atan2(sy, sx), math.atan2(17, 30), places=2)

    def test_per_report_like_macos(self):
        # Gain depends on counts per report only, not on timing.
        s = settings()
        a, b = MotionAccelerator(), MotionAccelerator()
        for i in range(20):
            a.process(30, 0, i * 0.016, s)
            b.process(30, 0, i * 0.004, s)
        self.assertEqual(a.last_gain, b.last_gain)
        self.assertAlmostEqual(a.last_gain, curve.MacCurve().px_per_count(30))

    def test_report_rate_normalization(self):
        # A 1000 Hz mouse normalized to 125 Hz: 5 counts/report behave like 40.
        s = settings(report_rate=125)
        acc = MotionAccelerator()
        for i in range(300):
            acc.process(5, 0, i * 0.001, s)
        self.assertAlmostEqual(acc.last_counts, 40, delta=1)
        self.assertAlmostEqual(acc.last_gain, curve.MacCurve().px_per_count(acc.last_counts))

    def test_zero_dt_is_harmless(self):
        s = settings()
        acc = MotionAccelerator()
        acc.process(3, 3, 1.0, s)
        acc.process(3, 3, 1.0, s)
        self.assertTrue(math.isfinite(acc.last_gain))


class FrameTest(unittest.TestCase):
    def test_buttons_and_unknown_events_pass_through(self):
        fp = FrameProcessor({REL_X, REL_Y}, settings())
        events = [(EV_KEY, 0x110, 1), (0x04, 0x04, 90001)]
        self.assertEqual(fp.process(events, 1.0), events)

    def test_passthrough_mode_is_identity(self):
        fp = FrameProcessor({REL_X, REL_Y, REL_WHEEL, REL_WHEEL_HI_RES}, settings(passthrough=True))
        events = [(EV_REL, REL_X, 7), (EV_REL, REL_Y, -3), (EV_REL, REL_WHEEL, 1), (EV_REL, REL_WHEEL_HI_RES, 120)]
        self.assertEqual(fp.process(events, 1.0), events)

    def test_scroll_disabled_passes_raw_wheel(self):
        fp = FrameProcessor({REL_X, REL_Y, REL_WHEEL}, settings(scroll_enabled=False))
        self.assertEqual(fp.process([(EV_REL, REL_WHEEL, -1)], 1.0), [(EV_REL, REL_WHEEL, -1)])


if __name__ == "__main__":
    unittest.main()

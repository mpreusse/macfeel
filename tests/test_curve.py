import math
import unittest

from macfeel import curve as c

# Measured on real Macs by INRIA's libpointing project (EchoMouse: a hardware
# device sends reports of exactly n counts at 400 cpi; cursor movement is read
# back). https://github.com/INRIA/libpointing, pointing-echomouse/, commit 46b0e70.
# {slider value: {counts per report: points per report}}
# Small n carry a measurement artifact of up to +1.7% (61 moves divided by 60).
SEQUOIA = {   # darwin-24: macOS 15.3.2, Apple Silicon
    0.0:    {1: 0.242, 3: 0.731, 5: 1.219, 10: 2.438, 20: 4.877, 40: 9.758, 60: 14.638, 80: 19.519, 100: 24.396, 120: 28.797},
    0.125:  {3: 0.687, 5: 1.414, 10: 3.137, 20: 6.545, 40: 15.989, 60: 26.485, 80: 34.824, 100: 41.441, 120: 48.059},
    0.6875: {3: 0.886, 5: 2.419, 10: 6.287, 20: 14.047, 40: 43.301, 60: 78.477, 80: 105.137, 100: 119.93, 120: 134.723},
    1.0:    {3: 0.909, 5: 2.947, 10: 8.034, 20: 18.213, 40: 58.621, 60: 107.605, 80: 144.199, 100: 163.531, 120: 182.867},
    3.0:    {3: 1.823, 5: 5.893, 10: 16.072, 20: 35.832, 40: 117.242, 60: 215.211, 80: 288.398, 100: 327.066, 120: 365.738},
}
SIERRA = {    # darwin-16: macOS 10.12
    0.6875: {10: 4.365, 20: 12.923, 40: 42.191, 60: 86.856, 80: 133.773, 100: 180.691, 120: 223.434},
    3.0:    {10: 17.323, 20: 65.078, 40: 264.512, 60: 610.559, 80: 982.301, 100: 1267.77, 120: 1499.85},
}


def points_per_report(cv, n):
    return cv.px_per_count(n) * n


class MeasuredMacOSTest(unittest.TestCase):
    def test_table_matches_macos_15(self):
        for tracking, data in SEQUOIA.items():
            cv = c.MacCurve(tracking, algorithm="table")
            for n, measured in data.items():
                ratio = measured / points_per_report(cv, n)
                # The artifact shows at every n for the two slowest steps,
                # elsewhere only below n=60.
                tolerance = 0.005 if n >= 60 and tracking >= 0.5 else 0.02
                self.assertAlmostEqual(ratio, 1.0, delta=tolerance, msg=f"tracking {tracking}, n={n}")

    def test_parametric_matches_macos_10_12(self):
        for tracking, data in SIERRA.items():
            cv = c.MacCurve(tracking, algorithm="parametric")
            for n, measured in data.items():
                ratio = measured / points_per_report(cv, n)
                self.assertAlmostEqual(ratio, 1.0, delta=0.02, msg=f"tracking {tracking}, n={n}")


class TableTest(unittest.TestCase):
    def test_default_table_decodes(self):
        curves = c.parse_table(c.DEFAULT_ACCEL_TABLE)
        self.assertEqual([a for a, _ in curves], [0.0, 1.0])
        self.assertEqual(curves[0][1], [(1.0, 1.0)])
        self.assertEqual(curves[1][1][2], (12.0, 95.0))
        self.assertEqual(len(curves[1][1]), 9)

    def test_tracking_zero_is_96_over_400(self):
        cv = c.MacCurve(0)
        for n in (1, 7, 50, 200):
            self.assertAlmostEqual(cv.px_per_count(n), 96 / 400)

    def test_resolution_scales_input(self):
        # At half the resolution, 20 counts are the same hand movement as 40 at 400 dpi.
        a, b = c.MacCurve(dpi=400), c.MacCurve(dpi=200)
        self.assertAlmostEqual(points_per_report(b, 20), points_per_report(a, 40), places=6)


class CurvePropertiesTest(unittest.TestCase):
    def curves(self):
        for algorithm in ("table", "parametric"):
            for preset in ("mac", "mac-light", "linear"):
                for tracking in c.MAC_SLIDER_STEPS + (0.3, 4.0):
                    for accel in (0, 0.5, 1, 2):   # 2 is clamped to 1
                        yield c.from_config({"preset": preset, "tracking_speed": tracking,
                                             "acceleration": accel, "algorithm": algorithm})

    def test_finite_positive_and_monotonic(self):
        # Whole counts, as in real reports (Apple floors fractional ones, which can
        # dip by a fraction of a pixel on the flat top of the curve).
        counts = list(range(0, 400))
        for cv in self.curves():
            prev = -1
            for n in counts + [1e6]:
                g = cv.px_per_count(n)
                self.assertTrue(math.isfinite(g) and g > 0, (cv, n, g))
                out = g * n
                self.assertGreaterEqual(out, prev - 1e-6, (cv, n))   # more counts never move less
                prev = out

    def test_slider_steps_and_default(self):
        self.assertEqual(list(c.MAC_SLIDER_STEPS), [row[0] for row in c.APPLE_CURVES])
        self.assertIn(c.DEFAULT_TRACKING, c.MAC_SLIDER_STEPS)

    def test_speed_scales_without_changing_shape(self):
        base, slow = c.from_config({}), c.from_config({"speed": 0.8})
        for n in (1, 10, 50, 120):
            self.assertAlmostEqual(slow.px_per_count(n) / base.px_per_count(n), 0.8)

    def test_lower_steps_accelerate_less_but_keep_slow_precision(self):
        lower, default = c.MacCurve(0.5), c.MacCurve()
        self.assertLess(points_per_report(lower, 60), points_per_report(default, 60))
        self.assertAlmostEqual(lower.px_per_count(1), default.px_per_count(1), delta=0.05)

    def test_linear_and_light(self):
        mac = c.from_config({})
        lin = c.from_config({"preset": "linear"})
        self.assertEqual(lin.px_per_count(1), lin.px_per_count(100))
        self.assertAlmostEqual(lin.px_per_count(1), mac.base)
        light = c.from_config({"preset": "mac-light"})
        self.assertLess(light.px_per_count(60), mac.px_per_count(60))


if __name__ == "__main__":
    unittest.main()

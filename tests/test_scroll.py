import unittest

from macfeel import scroll as s

LO, HI = 0x08, 0x0B


def spin(axis, notches_per_s, seconds, units_per_event=120, t0=0.0, accelerate=True, speed=1.0,
         direction=1):
    """Feed a steady wheel spin. Returns (hi-res total, low-res total, end time)."""
    events_per_notch = 120 // units_per_event
    dt = 1.0 / (notches_per_s * events_per_notch)
    hi = lo = 0
    t = t0
    for _ in range(int(seconds / dt)):
        t += dt
        wheel = {HI: direction * units_per_event}
        if units_per_event == 120:
            wheel[LO] = direction
        for _, code, value in axis.process(wheel, t, accelerate, s.DEFAULT_ACCELERATION, speed):
            if code == HI:
                hi += value
            else:
                lo += value
    return hi, lo, t


class AppleScrollAcceleratorTest(unittest.TestCase):
    """Numbers follow IOHIDScrollAccelerator with the macOS defaults."""

    def steady(self, interval_ms, events=12):
        acc = s.AppleScrollAccelerator()
        lines = None
        for i in range(events):
            lines = acc.lines(1, i * interval_ms / 1000)
        return lines

    def test_slow_notches_are_near_zero_before_rounding(self):
        # A gap > 150 ms ends the averaging window at that notch: count 1.
        self.assertAlmostEqual(self.steady(500), 0.0017, places=4)
        self.assertAlmostEqual(self.steady(200), 0.0017, places=4)
        acc = s.AppleScrollAccelerator()
        self.assertAlmostEqual(acc.lines(1, 0.0), 0.0017, places=4)   # first, isolated notch

    def test_fast_spinning(self):
        # The window stops summing at 500 ms: 5 notches at 100 ms -> boost sqrt(5).
        self.assertAlmostEqual(self.steady(100), 1.9970, places=3)   # 10 notches/s
        self.assertAlmostEqual(self.steady(50), 7.0728, places=3)    # 20 notches/s
        self.assertAlmostEqual(self.steady(10), 9.424, places=2)     # 100 notches/s

    def test_multiplier_never_below_one_and_rises_with_speed(self):
        prev = 0
        for interval in (400, 150, 100, 75, 50, 30, 15, 8, 4):
            acc = s.AppleScrollAccelerator()
            m = 0
            for i in range(12):
                m = acc.multiplier(1, i * interval / 1000)
            self.assertGreaterEqual(m, 1.0)
            self.assertGreaterEqual(m, prev)
            prev = m
        self.assertLess(prev, 10)

    def test_direction_change_starts_over(self):
        # After a reversal only the new notch counts (count 1); its own 10 ms
        # interval still makes a quick reversal count as fast.
        acc = s.AppleScrollAccelerator()
        for i in range(10):
            acc.lines(1, i * 0.02)
        expected = acc.table.multiplier((s.A * 10 ** 2 - s.B * 10 + s.C) * 1) * s.PIXEL_TO_WHEEL
        self.assertAlmostEqual(acc.lines(-1, 0.19), expected, places=6)


class ScrollAxisTest(unittest.TestCase):
    def test_slow_notches_pass_through_1_to_1(self):
        hi, lo, _ = spin(s.ScrollAxis(LO, HI, True), notches_per_s=2, seconds=5)
        self.assertEqual((hi, lo), (10 * 120, 10))

    def test_fast_spin_is_accelerated_like_macos(self):
        hi, lo, _ = spin(s.ScrollAxis(LO, HI, True), notches_per_s=20, seconds=1)
        self.assertGreater(hi / (20 * 120), 5)        # ~7x once the history fills
        self.assertEqual(lo, hi // 120)               # streams stay consistent

    def test_hires_wheel_matches_notch_wheel(self):
        # MX Master on Linux reports 1/8 notches (15 units); macOS sees whole notches.
        notch, _, _ = spin(s.ScrollAxis(LO, HI, True), notches_per_s=20, seconds=1)
        fine, _, _ = spin(s.ScrollAxis(LO, HI, True), notches_per_s=20, seconds=1, units_per_event=15)
        self.assertAlmostEqual(fine / notch, 1.0, delta=0.15)

    def test_pause_and_reversal_restart_at_1_to_1(self):
        axis = s.ScrollAxis(LO, HI, True)
        _, _, t = spin(axis, 20, 1)
        hi, _, t = spin(axis, 2, 2, t0=t + 1.0)        # after a pause: slow again
        self.assertEqual(hi, 4 * 120)
        spin(axis, 20, 1, t0=t)
        hi, _, _ = spin(axis, 2, 1, t0=t + 1.0 + 0.01, direction=-1)
        self.assertEqual(hi, -2 * 120)

    def test_speed_scales_distance(self):
        hi, lo, _ = spin(s.ScrollAxis(LO, HI, True), notches_per_s=2, seconds=5, speed=0.5)
        self.assertEqual((hi, lo), (5 * 120, 5))

    def test_no_acceleration_only_speed(self):
        hi, _, _ = spin(s.ScrollAxis(LO, HI, True), 20, 1, accelerate=False, speed=1.5)
        self.assertEqual(hi, 20 * 180)

    def test_lowres_only_device(self):
        axis = s.ScrollAxis(LO, HI, False)
        out = []
        for i in range(5):
            out += axis.process({LO: 1}, i * 0.5, True)
        self.assertEqual(out, [(s.EV_REL, LO, 1)] * 5)


if __name__ == "__main__":
    unittest.main()

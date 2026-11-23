import math
import unittest

from macfeel import scroll as s

LO, HI = 0x08, 0x0B


def run(axis, frames, strength):
    """frames: list of (t, {code: value}). Returns (sum hi, sum lo)."""
    hi = lo = 0
    for t, wheel in frames:
        for _, code, value in axis.process(wheel, t, strength):
            if code == HI:
                hi += value
            else:
                lo += value
    return hi, lo


class MultiplierTest(unittest.TestCase):
    def test_properties(self):
        prev = 0
        for i in range(0, 2000):
            rate = i / 10
            m = s.multiplier(rate, 1.0)
            self.assertTrue(math.isfinite(m))
            self.assertGreaterEqual(m, 1.0)
            self.assertGreaterEqual(m, prev)  # monotonic
            prev = m
        self.assertEqual(s.multiplier(50, 0.0), 1.0)
        self.assertEqual(s.multiplier(1.0, 1.0), 1.0)   # slow scrolling is 1:1


class ScrollAxisTest(unittest.TestCase):
    def test_slow_notches_pass_through_1_to_1(self):
        axis = s.ScrollAxis(LO, HI, True)
        frames = [(i * 0.5, {LO: -1, HI: -120}) for i in range(10)]  # 2 notches/s
        self.assertEqual(run(axis, frames, 1.0), (-1200, -10))

    def test_fast_spin_is_accelerated_and_streams_stay_consistent(self):
        axis = s.ScrollAxis(LO, HI, True)
        frames = [(i * 0.01, {HI: 30}) for i in range(100)]   # 25 notches/s, hi-res wheel
        hi, lo = run(axis, frames, 1.0)
        self.assertGreater(hi, 3000 * 2)
        self.assertEqual(lo, hi // 120)                      # one notch per 120 hi-res units

    def test_lowres_only_device(self):
        axis = s.ScrollAxis(LO, HI, False)
        out = []
        for i in range(5):
            out += axis.process({LO: 1}, i * 0.5, 1.0)
        self.assertEqual(out, [(s.EV_REL, LO, 1)] * 5)

    def test_direction_change_resets(self):
        axis = s.ScrollAxis(LO, HI, True)
        run(axis, [(i * 0.01, {HI: 60}) for i in range(50)], 1.0)
        hi, lo = run(axis, [(0.5 + 0.01, {HI: -120})], 1.0)
        self.assertEqual((hi, lo), (-120, -1))


if __name__ == "__main__":
    unittest.main()

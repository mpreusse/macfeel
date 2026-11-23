"""Scroll acceleration (pure logic, no I/O).

Units: the kernel reports high-resolution scrolling in 1/120 of a notch
(REL_WHEEL_HI_RES / REL_HWHEEL_HI_RES); a classic notch is 120. Low-res
REL_WHEEL / REL_HWHEEL carry whole notches.

We accelerate in hi-res units and then derive both output streams from the
result, exactly like the kernel does: one low-res notch per 120 accumulated
hi-res units. That keeps smooth-scrolling apps (GTK, Firefox: read hi-res)
and notch-based apps (Electron, Flutter: read low-res) in agreement.
"""

import math

EV_REL = 0x02
NOTCH = 120

GAP_RESET = 0.25   # s; a pause this long ends a scroll gesture
RATE_TAU = 0.10    # s; smoothing of the scroll rate estimate


def multiplier(rate, strength):
    """Gain for a scroll rate given in notches/second.

    Slow, deliberate scrolling (a few notches per second) stays 1:1; fast
    spinning/flicking gets progressively multiplied. `strength` 0 means no
    acceleration, 1 is the default Mac-like amount.

    PROVISIONAL shape until calibrated against macOS (see README).
    """
    if strength <= 0 or rate <= 0:
        return 1.0
    excess = max(0.0, rate - 3.0)           # below 3 notches/s: no acceleration
    gain = 1.0 + strength * 0.15 * excess ** 1.2
    return min(gain, 1.0 + 11.0 * strength)  # cap so a frantic spin stays controllable


class ScrollAxis:
    """One wheel axis (vertical or horizontal)."""

    def __init__(self, lo_code, hi_code, has_hires):
        self.lo_code, self.hi_code, self.has_hires = lo_code, hi_code, has_hires
        self.reset()

    def reset(self):
        self.last_t = None
        self.direction = 0
        self.rate = 0.0        # notches/s, smoothed
        self.hi_rem = 0.0      # fractional hi-res units not emitted yet
        self.lo_acc = 0        # hi-res units not yet turned into a low-res notch

    def process(self, wheel, t, strength):
        """`wheel`: {code: value} for this frame. Returns a list of (EV_REL, code, value)."""
        if self.hi_code in wheel:
            units = wheel[self.hi_code]
        elif self.lo_code in wheel:
            units = wheel[self.lo_code] * NOTCH
        else:
            return []
        if units == 0:
            return []

        direction = 1 if units > 0 else -1
        dt = None if self.last_t is None else t - self.last_t
        self.last_t = t
        if dt is None or dt > GAP_RESET or dt <= 0 or direction != self.direction:
            # New gesture (or reversal): start from 1:1.
            self.direction = direction
            self.rate = 0.0
            self.hi_rem = 0.0
            self.lo_acc = 0
        else:
            instant = abs(units) / NOTCH / dt
            alpha = 1.0 - math.exp(-dt / RATE_TAU)
            self.rate += alpha * (instant - self.rate)

        return self.emit(units * multiplier(self.rate, strength))

    def emit(self, units):
        self.hi_rem += units
        hi = round(self.hi_rem)
        self.hi_rem -= hi
        if hi == 0:
            return []
        out = []
        if self.has_hires:
            out.append((EV_REL, self.hi_code, hi))
        self.lo_acc += hi
        lo = int(self.lo_acc / NOTCH)          # truncate toward zero, like the kernel
        if lo:
            self.lo_acc -= lo * NOTCH
            out.append((EV_REL, self.lo_code, lo))
        return out

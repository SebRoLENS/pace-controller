"""Recognition of a stable residual when a zero target is not attainable."""

from collections import deque
from math import isfinite


class ZeroTargetStability:
    """Accept a near-zero plateau, never a stalled pressure outside the tolerance."""

    def __init__(self, maximum_gap_seconds: float = 3.0) -> None:
        self.tolerance_bar = 0.5
        self.duration_seconds = 10.0
        self.band_bar = 0.01
        self.maximum_gap_seconds = maximum_gap_seconds
        self.samples: deque[tuple[float, float]] = deque()

    def reset(self) -> None:
        self.samples.clear()

    def add(self, timestamp: float, pressure_bar: float, target_bar: float, control: bool) -> bool:
        if (
            not control
            or target_bar != 0.0
            or not isfinite(timestamp)
            or not isfinite(pressure_bar)
            or abs(pressure_bar) > self.tolerance_bar + 1e-9
        ):
            self.reset()
            return False
        if self.samples:
            gap = timestamp - self.samples[-1][0]
            if gap <= 0 or gap > self.maximum_gap_seconds:
                self.reset()
        self.samples.append((timestamp, pressure_bar))
        cutoff = timestamp - self.duration_seconds
        # Retain the point spanning the beginning of the observation interval.
        while len(self.samples) > 1 and self.samples[1][0] <= cutoff:
            self.samples.popleft()
        elapsed = timestamp - self.samples[0][0]
        pressures = [value for _, value in self.samples]
        return elapsed >= self.duration_seconds and max(pressures) - min(pressures) <= self.band_bar + 1e-9

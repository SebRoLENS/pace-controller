"""Rolling pressure-loss assessment for sample and inlet telemetry."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import islice
from statistics import median
from math import fsum, inf, isfinite

from .models import LeakThresholds


def control_autonomy_hours(
    source_pressure_bar: float,
    sample_pressure_bar: float,
    loss_rate_bar_min: float,
) -> float | None:
    """Estimate CONTROL duration from the source-to-sample pressure headroom."""
    if not (isfinite(source_pressure_bar) and isfinite(sample_pressure_bar)):
        return None
    available_pressure = max(0.0, source_pressure_bar - sample_pressure_bar)
    rate_bar_hour = loss_rate_bar_min * 60.0
    if rate_bar_hour <= 0:
        return inf
    return available_pressure / rate_bar_hour


@dataclass(slots=True)
class LeakAssessment:
    level: str
    rate_bar_min: float = 0.0
    observation_minutes: float = 0.0
    history_reset: bool = False


class LeakMonitor:
    """Display the loss rate fitted to pressure readings in a rolling window."""

    def __init__(self, thresholds: LeakThresholds, window_minutes: float = 5.0) -> None:
        if not isfinite(window_minutes) or window_minutes <= 0:
            raise ValueError("Averaging window must be finite and positive")
        self.window_seconds = window_minutes * 60.0
        self.thresholds = thresholds
        self.samples: deque[tuple[float, float]] = deque()
        self.started_at: float | None = None

    def reset(self) -> None:
        self.samples.clear()
        self.started_at = None

    def update_thresholds(self, thresholds: LeakThresholds) -> None:
        thresholds.validate()
        self.thresholds = thresholds
        self.reset()

    def add(self, timestamp: float, value: float, enabled: bool) -> LeakAssessment:
        if not enabled:
            self.reset()
            return LeakAssessment("paused_control")
        if not (isfinite(timestamp) and isfinite(value)):
            return LeakAssessment("assessing")
        if self.samples and timestamp <= self.samples[-1][0]:
            return LeakAssessment("assessing")
        history_reset = self._is_pressure_step(timestamp, value)
        if history_reset:
            self.reset()
        if self.started_at is None:
            self.started_at = timestamp
        self.samples.append((timestamp, value))
        cutoff = timestamp - self.window_seconds
        # Keep one point at/before the boundary for interpolation when polling
        # times do not land exactly on the rolling-window cutoff.
        while len(self.samples) > 1 and self.samples[1][0] <= cutoff:
            self.samples.popleft()
        if len(self.samples) < 2:
            return LeakAssessment("assessing", history_reset=history_reset)

        start, start_value = self.samples[0]
        if start < cutoff:
            next_time, next_value = self.samples[1]
            fraction = (cutoff - start) / (next_time - start)
            start_value += fraction * (next_value - start_value)
            start = cutoff
        elapsed_minutes = (timestamp - start) / 60.0
        # Fit all readings inside the window, including its interpolated
        # boundary when needed. Relative times avoid large epoch timestamps.
        points = [(start, start_value), *list(self.samples)[1:]]
        xs = [(stamp - start) / 60.0 for stamp, _ in points]
        ys = [pressure for _, pressure in points]
        mean_x = fsum(xs) / len(xs)
        mean_y = fsum(ys) / len(ys)
        denominator = fsum((x - mean_x) ** 2 for x in xs)
        slope = fsum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
        rate = max(0.0, -slope)

        t = self.thresholds
        green_rate = t.reference_drop_bar / t.green_minutes
        yellow_rate = t.reference_drop_bar / t.yellow_minutes
        orange_rate = t.reference_drop_bar / t.orange_minutes

        if rate > orange_rate:
            return LeakAssessment("significant_leak", rate, elapsed_minutes)
        if rate > yellow_rate:
            return LeakAssessment("pressure_leak", rate, elapsed_minutes)
        if rate > green_rate:
            return LeakAssessment("slight_leak", rate, elapsed_minutes)
        # Green confirmation uses total uninterrupted monitoring time, not
        # window length: its configurable default is longer than the averaging window.
        if (timestamp - self.started_at) / 60.0 >= t.green_minutes:
            return LeakAssessment("no_leak", rate, elapsed_minutes)
        return LeakAssessment("assessing", rate, elapsed_minutes)

    def _is_pressure_step(self, timestamp: float, value: float) -> bool:
        """Detect a discontinuity relative to recent drift and measurement noise."""
        if len(self.samples) < 3:
            return False
        recent = list(islice(reversed(self.samples), 11))
        recent.reverse()
        rates = [(p2 - p1) / (t2 - t1) for (t1, p1), (t2, p2) in zip(recent, recent[1:])]
        drift = median(rates)
        noise = median(abs(rate - drift) for rate in rates)
        previous_time, previous_value = recent[-1]
        interval = timestamp - previous_time
        departure = abs(value - previous_value - drift * interval)
        # A 0.1 bar floor avoids reacting to small fluctuations. Scale the
        # threshold to recent rate variability and actual polling duration.
        return departure > max(0.1, 8.0 * noise * interval) + 1e-9

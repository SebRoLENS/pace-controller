"""Rolling pressure-loss assessment for sample and inlet telemetry."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import inf, isfinite

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


class LeakMonitor:
    def __init__(
        self, thresholds: LeakThresholds, minimum_observation_minutes: float = 3.0
    ) -> None:
        self.thresholds = thresholds
        if minimum_observation_minutes <= 0:
            raise ValueError("Minimum observation time must be positive")
        self.minimum_observation_minutes = minimum_observation_minutes
        self.samples: deque[tuple[float, float]] = deque()

    def reset(self) -> None:
        self.samples.clear()

    def update_thresholds(self, thresholds: LeakThresholds) -> None:
        thresholds.validate()
        self.thresholds = thresholds
        self.reset()

    def add(self, timestamp: float, value: float, enabled: bool) -> LeakAssessment:
        if not enabled:
            self.reset()
            return LeakAssessment("paused_control")
        if not isfinite(value):
            return LeakAssessment("assessing")
        self.samples.append((timestamp, value))
        window_seconds = self.thresholds.green_minutes * 60.0
        while self.samples and timestamp - self.samples[0][0] > window_seconds:
            self.samples.popleft()
        if len(self.samples) < 2:
            return LeakAssessment("assessing")

        elapsed_minutes = (self.samples[-1][0] - self.samples[0][0]) / 60.0
        if elapsed_minutes <= 0:
            return LeakAssessment("assessing")
        rate = max(0.0, -self._linear_slope_bar_min())
        if elapsed_minutes < self.minimum_observation_minutes:
            return LeakAssessment("assessing")

        t = self.thresholds
        green_rate = t.reference_drop_bar / t.green_minutes
        yellow_rate = t.reference_drop_bar / t.yellow_minutes
        orange_rate = t.reference_drop_bar / t.orange_minutes

        if rate > orange_rate:
            return LeakAssessment("significant_leak", rate, elapsed_minutes)
        if elapsed_minutes >= t.orange_minutes and rate > yellow_rate:
            return LeakAssessment("pressure_leak", rate, elapsed_minutes)
        if elapsed_minutes >= t.yellow_minutes and rate > green_rate:
            return LeakAssessment("slight_leak", rate, elapsed_minutes)
        if elapsed_minutes >= t.green_minutes and rate <= green_rate:
            return LeakAssessment("no_leak", rate, elapsed_minutes)
        return LeakAssessment("assessing", rate, elapsed_minutes)

    def _linear_slope_bar_min(self) -> float:
        origin = self.samples[0][0]
        xs = [(stamp - origin) / 60.0 for stamp, _ in self.samples]
        ys = [value for _, value in self.samples]
        count = len(xs)
        mean_x = sum(xs) / count
        mean_y = sum(ys) / count
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator == 0:
            return 0.0
        return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator

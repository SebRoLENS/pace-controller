"""Typed data shared by the controller engine and graphical interface."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from math import isfinite, nan
from typing import Any


class ConnectionKind(str, Enum):
    ETHERNET = "ethernet"
    SERIAL = "serial"
    SIMULATOR = "simulator"


@dataclass(slots=True)
class ConnectionConfig:
    kind: ConnectionKind = ConnectionKind.ETHERNET
    host: str = "192.168.10.2"
    port: int = 5025
    serial_port: str = ""
    baud_rate: int = 9600
    parity: str = "N"
    flow_control: str = "none"
    terminator: str = "\r"
    timeout: float = 2.0
    auto_configure_network: bool = True


@dataclass(slots=True)
class ControlParameters:
    mode: str = "ACTIVE"
    overshoot: bool = False
    in_limit_percent: float = 0.01
    in_limit_time_seconds: int = 2
    vent_rate_bar_s: float = 0.1


@dataclass(slots=True)
class PressureStep:
    target_bar: float
    slew_bar_s: float
    dwell_seconds: float = 0.0
    maximum_rate: bool = False
    note: str = ""

    @classmethod
    def from_mapping(cls, item: dict[str, Any]) -> "PressureStep":
        return cls(
            target_bar=float(item.get("target_bar", item.get("Target", 0.0))),
            slew_bar_s=float(item.get("slew_bar_s", item.get("Slew", 0.1))),
            dwell_seconds=float(item.get("dwell_seconds", item.get("Dwell", 0.0))),
            maximum_rate=bool(item.get("maximum_rate", False)),
            note=str(item.get("note", item.get("Note", ""))),
        )

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Telemetry:
    timestamp: float = 0.0
    current_pressure_bar: float = nan
    target_pressure_bar: float = nan
    positive_source_bar: float = nan
    negative_source_bar: float = nan
    measured_slew_bar_s: float = nan
    valve_effort_percent: float = nan
    control: bool = False
    in_limits: bool = False
    zero_target_stable: bool = False
    source_margin_bar: float = nan


@dataclass(slots=True)
class DeviceCapabilities:
    identity: str = ""
    range_min_bar: float = nan
    range_max_bar: float = nan
    module: int = 1


@dataclass(slots=True)
class LeakThresholds:
    green_max_bar_hour: float = 0.1
    yellow_max_bar_hour: float = 0.3
    orange_max_bar_hour: float = 0.6

    def validate(self) -> None:
        values = (self.green_max_bar_hour, self.yellow_max_bar_hour, self.orange_max_bar_hour)
        if not all(isfinite(value) for value in values) or not 0 < values[0] < values[1] < values[2]:
            raise ValueError("Thresholds in bar/h must satisfy 0 < green < yellow < orange.")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LeakThresholds":
        if any(key in raw for key in cls.__dataclass_fields__):
            result = cls(**{key: float(value) for key, value in raw.items() if key in cls.__dataclass_fields__})
        elif "reference_drop_bar" in raw:
            old = tuple(float(raw.get(key, default)) for key, default in (
                ("reference_drop_bar", 0.005), ("green_minutes", 10),
                ("yellow_minutes", 5), ("orange_minutes", 1),
            ))
            if old == (0.005, 10.0, 5.0, 1.0):
                result = cls()
            else:
                reference, green, yellow, orange = old
                if not 0 < orange < yellow < green or not isfinite(reference) or reference <= 0:
                    raise ValueError("Invalid legacy leak thresholds")
                result = cls(*(reference * 60.0 / minutes for minutes in (green, yellow, orange)))
        else:
            result = cls()
        result.validate()
        return result


@dataclass(slots=True)
class AppSettings:
    language: str = "en"
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    leak_thresholds: LeakThresholds = field(default_factory=LeakThresholds)
    minimum_source_margin_bar: float = 2.0
    source_margin_rearm_bar: float = 2.2


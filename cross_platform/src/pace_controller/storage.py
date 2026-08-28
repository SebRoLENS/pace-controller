"""Persistent application settings, logs, and telemetry storage."""

from __future__ import annotations

import csv
import json
import os
import platform
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    AppSettings,
    ConnectionConfig,
    ConnectionKind,
    LeakThresholds,
    Telemetry,
)


def data_directory() -> Path:
    override = os.environ.get("PACE_CONTROLLER_DATA_DIR")
    if override:
        path = Path(override).expanduser()
    elif platform.system() == "Windows":
        path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PACE Controller"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "pace-controller"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _coerce_connection(raw: dict[str, object]) -> ConnectionConfig:
    data = dict(raw)
    try:
        data["kind"] = ConnectionKind(str(data.get("kind", "ethernet")))
    except ValueError:
        data["kind"] = ConnectionKind.ETHERNET
    allowed = ConnectionConfig.__dataclass_fields__
    return ConnectionConfig(**{key: value for key, value in data.items() if key in allowed})


def load_settings() -> AppSettings:
    path = data_directory() / "PACE_controller_settings.json"
    if not path.exists():
        return AppSettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        connection = _coerce_connection(dict(raw.get("connection", {})))
        leak_raw = dict(raw.get("leak_thresholds", {}))
        leak_allowed = LeakThresholds.__dataclass_fields__
        leak = LeakThresholds(
            **{key: value for key, value in leak_raw.items() if key in leak_allowed}
        )
        leak.validate()
        return AppSettings(
            language=str(raw.get("language", "en")),
            connection=connection,
            leak_thresholds=leak,
            minimum_source_margin_bar=float(raw.get("minimum_source_margin_bar", 2.0)),
            source_margin_rearm_bar=float(raw.get("source_margin_rearm_bar", 2.2)),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    path = data_directory() / "PACE_controller_settings.json"
    payload = asdict(settings)
    payload["connection"]["kind"] = settings.connection.kind.value
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class DataLogger:
    """Thread-safe diagnostic and CSV logger."""

    CSV_HEADER = [
        "Timestamp_UTC",
        "Pressure_bar",
        "Target_bar",
        "Source_positive_bar",
        "Source_negative_bar",
        "Actual_slew_bar_s",
        "Effort_percent",
        "Control",
        "In_limit",
    ]

    def __init__(self) -> None:
        self.directory = data_directory()
        self.log_path = self.directory / "PACE_controller_log.txt"
        self.csv_path = self.directory / "PACE_controller_data.csv"
        self._lock = threading.Lock()

    def log(self, message: str) -> str:
        stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
        line = f"{stamp}  {message}"
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return line

    def telemetry(self, item: Telemetry) -> None:
        new_file = not self.csv_path.exists()
        stamp = datetime.fromtimestamp(item.timestamp, timezone.utc).isoformat(timespec="milliseconds")
        row = [
            stamp,
            item.current_pressure_bar,
            item.target_pressure_bar,
            item.positive_source_bar,
            item.negative_source_bar,
            item.measured_slew_bar_s,
            item.valve_effort_percent,
            int(item.control),
            int(item.in_limits),
        ]
        with self._lock:
            with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                if new_file:
                    writer.writerow(self.CSV_HEADER)
                writer.writerow(row)


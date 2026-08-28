from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from pace_controller import __version__
from pace_controller.i18n import STRINGS
from pace_controller.leak import LeakMonitor
from pace_controller.models import LeakThresholds
from pace_controller.service import scpi_float, scpi_number, scpi_numbers, scpi_payload
from pace_controller.transports import SimulatorTransport


ROOT = Path(__file__).resolve().parents[2]
PROJECT = Path(__file__).resolve().parents[1]


def test_legacy_windows_controller_is_untouched() -> None:
    expected = (PROJECT / "LEGACY_SHA256.txt").read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256((ROOT / "PACE_Controller.ps1").read_bytes()).hexdigest()
    assert actual == expected


def test_metadata_version_matches() -> None:
    pyproject = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    readme = (PROJECT / "README.md").read_text(encoding="utf-8")
    manual = (PROJECT / "docs" / "PACE_Controller_Manual.md").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in pyproject
    assert f"Current version: **{__version__}**" in readme
    assert f"version **{__version__}**" in manual


def test_translations_have_identical_keys() -> None:
    assert set(STRINGS["en"]) == set(STRINGS["it"])


def test_scpi_parsing_and_formatting() -> None:
    assert scpi_number(":SENS1:PRES 2.500000E+01") == 25.0
    assert scpi_numbers('0,"No error"') == [0.0]
    assert scpi_payload(":UNIT1:PRES BAR") == "BAR"
    assert scpi_float(0.00000123456789) == "1.23456789e-06"
    with pytest.raises(ValueError):
        scpi_float(float("nan"))


def test_simulator_accepts_same_scpi_as_real_transport() -> None:
    device = SimulatorTransport()
    device.connect()
    assert "PACE6000" in device.query("*IDN?")
    device.write(":SOUR1:PRES:SLEW 10")
    device.write(":SOUR1:PRES 26")
    device.write(":OUTP1:STAT ON")
    time.sleep(0.15)
    assert scpi_number(device.query(":SENS1:PRES:CONT?")) > 25.0
    device.write(":OUTP1:STAT OFF")
    assert scpi_number(device.query(":OUTP1:STAT?")) == 0
    device.close()


@pytest.mark.parametrize(
    ("elapsed", "drop", "expected"),
    [
        (600.0, 0.001, "no_leak"),
        (300.0, 0.004, "slight_leak"),
        (60.0, 0.003, "pressure_leak"),
        (30.0, 0.004, "significant_leak"),
    ],
)
def test_leak_classification(elapsed: float, drop: float, expected: str) -> None:
    monitor = LeakMonitor(LeakThresholds())
    monitor.add(0.0, 10.0, True)
    result = monitor.add(elapsed, 10.0 - drop, True)
    assert result.level == expected


def test_leak_monitor_pauses_during_control() -> None:
    monitor = LeakMonitor(LeakThresholds())
    monitor.add(0.0, 10.0, True)
    assert monitor.add(1.0, 10.0, False).level == "paused_control"
    assert not monitor.samples

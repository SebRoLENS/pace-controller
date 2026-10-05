import pytest

from pace_controller.models import ControlParameters, PressureStep, Telemetry
from pace_controller.service import PaceService
from pace_controller.stability import ZeroTargetStability


def test_zero_accepts_stable_residual_only_after_ten_seconds():
    monitor = ZeroTargetStability()
    for timestamp in range(10):
        assert not monitor.add(timestamp, 0.5, 0.0, True)
    assert monitor.add(10.0, 0.5, 0.0, True)
    assert not monitor.add(11.0, 0.48, 0.0, True)


@pytest.mark.parametrize("pressure,target,control", [
    (0.51, 0.0, True), (5.0, 0.0, True),
    (0.5, 0.1, True), (0.5, 0.0, False), (float("nan"), 0.0, True),
])
def test_zero_rejects_outside_tolerance_nonzero_target_and_invalid_readings(pressure, target, control):
    monitor = ZeroTargetStability()
    for timestamp in range(15):
        assert not monitor.add(timestamp, pressure, target, control)


def test_zero_does_not_accept_a_slow_ramp_or_missing_telemetry():
    monitor = ZeroTargetStability()
    for timestamp in range(20):
        assert not monitor.add(timestamp, 0.49 - timestamp * 0.002, 0.0, True)
    monitor.reset()
    for timestamp in range(11):
        stable = monitor.add(timestamp, 0.49 + (0.002 if timestamp % 2 else 0.0), 0.0, True)
    assert stable
    assert not monitor.add(20.0, 0.49, 0.0, True)
    assert not monitor.add(21.0, 0.49, 0.0, True)


@pytest.mark.parametrize("target,stable,dwell,expected", [
    (0.0, True, 0.0, "complete"),
    (0.0, True, 5.0, "dwelling"),
    (0.0, False, 0.0, "waiting"),
    (1.0, True, 0.0, "waiting"),
])
def test_automation_accepts_zero_plateau_without_hardware_in_limit(monkeypatch, tmp_path, target, stable, dwell, expected):
    monkeypatch.setenv("PACE_CONTROLLER_DATA_DIR", str(tmp_path))
    service = PaceService()
    service._telemetry = Telemetry(current_pressure_bar=0.5, target_pressure_bar=target, control=True, in_limits=False, zero_target_stable=stable)
    service._automation = {"steps": [PressureStep(target, 0.1, dwell)], "index": 0, "state": "waiting", "deadline": 100.0}
    completed = []
    service._complete_step = lambda: completed.append(True)
    service._process_automation(20.0)
    if expected == "complete":
        assert completed
    else:
        assert not completed
        assert service._automation["state"] == expected


def test_poll_recognizes_zero_plateau_and_preserves_true_pressure(monkeypatch, tmp_path):
    monkeypatch.setenv("PACE_CONTROLLER_DATA_DIR", str(tmp_path))
    service = PaceService()
    service._connected = True
    clock = [0.0]
    monkeypatch.setattr("pace_controller.service.time.monotonic", lambda: clock[0])
    values = {
        ":SENS1:PRES:CONT?": "0.5", ":SOUR1:PRES?": "0",
        ":OUTP1:STAT?": "1", ":SOUR1:PRES:COMP1?": "50",
        ":SENS1:PRES:INL?": "0",
    }
    service._query = lambda command: values.get(command, "0")
    for timestamp in range(11):
        clock[0] = float(timestamp)
        service._poll()
    assert service.telemetry.zero_target_stable
    assert not service.telemetry.in_limits
    assert service.telemetry.current_pressure_bar == 0.5
    service._write = lambda command: None
    service._assert_no_error = lambda: None
    service._apply_pressure_step(PressureStep(0.0, 0.1), ControlParameters())
    assert not service.telemetry.zero_target_stable
    assert not service._zero_target_monitor.samples

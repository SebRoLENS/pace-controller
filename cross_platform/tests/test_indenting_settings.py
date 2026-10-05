import os

import pytest

from pace_controller.leak import LeakMonitor
from pace_controller.models import ControlParameters, LeakThresholds, PressureStep, Telemetry
from pace_controller.service import PaceService
from pace_controller.storage import load_settings, save_settings
from pace_controller.models import AppSettings


@pytest.mark.parametrize("rate,level", [(0.0,"no_leak"),(0.099,"no_leak"),(0.1,"slight_leak"),(0.299,"slight_leak"),(0.3,"pressure_leak"),(0.6,"pressure_leak"),(0.601,"significant_leak")])
def test_hourly_threshold_boundaries(rate, level):
    monitor = LeakMonitor(LeakThresholds())
    monitor.add(0, 50, True)
    assert monitor.add(60, 50-rate/60, True).level == level


def test_migration_and_settings_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("PACE_CONTROLLER_DATA_DIR", str(tmp_path))
    old = dict(reference_drop_bar=0.005, green_minutes=10, yellow_minutes=5, orange_minutes=1)
    assert LeakThresholds.from_dict(old) == LeakThresholds(0.1,0.3,0.6)
    old["reference_drop_bar"] = 0.01
    assert LeakThresholds.from_dict(old) == LeakThresholds(0.06,0.12,0.6)
    settings = AppSettings(leak_thresholds=LeakThresholds(0.2,0.5,1.0))
    save_settings(settings)
    assert load_settings().leak_thresholds == settings.leak_thresholds
    for limits in [(0.3,0.1,0.6),(0.1,0.3,float("nan")),(0.1,0.1,0.6)]:
        with pytest.raises(ValueError):
            LeakThresholds(*limits).validate()


@pytest.fixture
def cycle(monkeypatch, tmp_path):
    monkeypatch.setenv("PACE_CONTROLLER_DATA_DIR", str(tmp_path))
    service = PaceService()
    service._connected = True
    service._transport = object()
    service._telemetry = Telemetry(current_pressure_bar=0.5, positive_source_bar=50)
    writes = []
    monkeypatch.setattr(service, "_apply_pressure_step", lambda step, parameters: writes.append(step))
    monkeypatch.setattr(service, "_set_measure", lambda: None)
    service._start_sequence("INDENTING", [PressureStep(2,0.1,120),PressureStep(0,0.2,0)], ControlParameters(), False)
    return service, writes


def test_indenting_public_command_uses_separate_rates_and_wait(monkeypatch, tmp_path):
    monkeypatch.setenv("PACE_CONTROLLER_DATA_DIR", str(tmp_path))
    service = PaceService()
    service.start_indenting(2,0.1,ControlParameters(),0.3,80)
    command, args = service._commands.get_nowait()
    assert command == "sequence"
    assert args[1][0].slew_bar_s == 0.1
    assert args[1][1].slew_bar_s == 0.3
    assert args[1][0].dwell_seconds == 80


def test_pause_compression_freezes_deadline_and_resumes_same_stage(cycle, monkeypatch):
    service, writes = cycle
    monkeypatch.setattr("pace_controller.service.time.monotonic", lambda: 10)
    service._pause_indenting()
    assert writes[-1].target_bar == 0.5
    assert service._automation["state"] == "paused"
    service._process_automation(10000)
    assert service._automation["state"] == "paused"
    service._resume_indenting(3,0.15,0.4,90)
    assert service._automation["index"] == 0
    assert service._automation["state"] == "waiting"
    assert writes[-1].target_bar == 3
    assert service._automation["steps"][1].slew_bar_s == 0.4


def test_pause_hold_excludes_paused_time_and_preserves_elapsed_wait(cycle, monkeypatch):
    service, writes = cycle
    service._telemetry.in_limits = True
    service._process_automation(20)
    assert service._automation["state"] == "dwelling"
    monkeypatch.setattr("pace_controller.service.time.monotonic", lambda: 50)
    service._pause_indenting()
    assert service._automation["dwell_elapsed"] == 30
    monkeypatch.setattr("pace_controller.service.time.monotonic", lambda: 500)
    service._resume_indenting(3,0.1,0.2,120)
    service._telemetry.in_limits = False
    service._process_automation(510)
    assert service._automation["state"] == "waiting"
    service._telemetry.in_limits = True
    service._process_automation(520)
    assert service._automation["dwell_end"] == 610
    monkeypatch.setattr("pace_controller.service.time.monotonic", lambda: 540)
    service._pause_indenting()
    assert service._automation["dwell_elapsed"] == 50
    service._resume_indenting(3,0.1,0.2,80)
    service._process_automation(600)
    assert service._automation["dwell_end"] == 630


def test_pause_decompression_retains_return_phase_and_invalid_resume_stays_paused(cycle, monkeypatch):
    service, writes = cycle
    service._automation["index"] = 1
    monkeypatch.setattr("pace_controller.service.time.monotonic", lambda: 10)
    service._pause_indenting()
    with pytest.raises(ValueError):
        service._resume_indenting(3,-0.1,0.4,90)
    assert service._automation["state"] == "paused"
    service._resume_indenting(3,0.15,0.4,90)
    assert service._automation["index"] == 1
    assert writes[-1].target_bar == 0
    assert writes[-1].slew_bar_s == 0.4


def test_resume_rechecks_source_margin_and_keeps_paused(cycle):
    service, writes = cycle
    service._pause_indenting()
    service._telemetry.positive_source_bar = 3.5
    with pytest.raises(ValueError):
        service._resume_indenting(3, 0.1, 0.2, 120)
    assert service._automation["state"] == "paused"
    assert writes[-1].target_bar == 0.5


def test_ui_settings_and_pause_editability(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from pace_controller.ui import MainWindow
    monkeypatch.setenv("PACE_CONTROLLER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(PaceService,"start",lambda self: None)
    monkeypatch.setattr(PaceService,"shutdown",lambda self: None)
    app=QApplication.instance() or QApplication([])
    window=MainWindow(PaceService(),AppSettings())
    try:
        window.on_connection_changed(True,{"key":"connected"})
        assert [edit.value() for edit in window.leak_rate_spins] == [0.1,0.3,0.6]
        assert window.indent_dwell_spin.value() == 120
        window.set_busy(True)
        window.on_automation({"key":"moving","mode":"INDENTING","index":1,"total":2,"target":2})
        assert window.pause_indent_button.isEnabled()
        assert not window.indent_target_edit.isEnabled()
        window.on_automation({"key":"indenting_paused","mode":"INDENTING","index":1,"total":2})
        assert window.resume_indent_button.isEnabled()
        assert window.indent_target_edit.isEnabled()
        assert window.indent_decompression_edit.isEnabled()
        window.set_busy(False)
        assert not window.resume_indent_button.isEnabled()
    finally:
        window.close()
        app.processEvents()

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from pace_controller import __version__
from pace_controller import network
from pace_controller.external import host_environment
from pace_controller.i18n import STRINGS
from pace_controller.leak import LeakMonitor, control_autonomy_hours
from pace_controller.models import LeakThresholds
from pace_controller.service import scpi_float, scpi_number, scpi_numbers, scpi_payload
from pace_controller.transports import SimulatorTransport, TcpTransport


ROOT = Path(__file__).resolve().parents[2]
PROJECT = Path(__file__).resolve().parents[1]


def test_legacy_windows_controller_is_untouched() -> None:
    expected = (PROJECT / "LEGACY_SHA256.txt").read_text(encoding="utf-8").split()[0]
    canonical = (ROOT / "PACE_Controller.ps1").read_bytes().replace(b"\r\n", b"\n")
    actual = hashlib.sha256(canonical).hexdigest()
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


def test_frozen_environment_is_removed_before_opening_host_links() -> None:
    cleaned = host_environment(
        {
            "PATH": "/usr/bin",
            "LD_LIBRARY_PATH": "/tmp/frozen",
            "LD_LIBRARY_PATH_ORIG": "/usr/lib",
            "QT_PLUGIN_PATH": "/tmp/plugins",
            "APPIMAGE": "/tmp/PACE.AppImage",
        }
    )
    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["LD_LIBRARY_PATH"] == "/usr/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in cleaned
    assert "QT_PLUGIN_PATH" not in cleaned
    assert "APPIMAGE" not in cleaned


def test_zenodo_sync_applies_doi_metadata(tmp_path: Path) -> None:
    script_path = ROOT / ".github" / "scripts" / "sync_zenodo_doi.py"
    spec = importlib.util.spec_from_file_location("pace_zenodo_sync", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    readme = tmp_path / "README.md"
    citation = tmp_path / "CITATION.cff"
    readme.write_text(
        "\n".join(
            [
                "# PACE Controller",
                "",
                "[![Version](https://img.shields.io/github/v/release/SebRoLENS/pace-controller)](https://github.com/SebRoLENS/pace-controller/releases/latest)",
                "[![DOI](https://img.shields.io/badge/DOI-pending-lightgrey)](https://github.com/SebRoLENS/pace-controller/releases/latest)",
                "",
                "## Citation",
                "",
                "Pending.",
                "",
                "## License and independence",
                "",
                "MIT",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    citation.write_text(
        "\n".join(
            [
                "cff-version: 1.2.0",
                'version: "1.0.1"',
                'repository-code: "https://github.com/SebRoLENS/pace-controller"',
                'url: "https://github.com/SebRoLENS/pace-controller/releases/tag/v1.0.1"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    module.README = readme
    module.CITATION = citation
    module.apply_metadata("1.0.1", "10.5281/zenodo.12345678")

    updated_readme = readme.read_text(encoding="utf-8")
    updated_citation = citation.read_text(encoding="utf-8")
    assert "https://doi.org/10.5281/zenodo.12345678" in updated_readme
    assert 'doi: "10.5281/zenodo.12345678"' in updated_citation
    assert 'url: "https://doi.org/10.5281/zenodo.12345678"' in updated_citation


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


def test_tcp_transport_matches_validated_pace_line_endings() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()
    received: list[bytes] = []
    server_errors: list[BaseException] = []

    def receive_command(connection: socket.socket) -> bytes:
        payload = bytearray()
        while not payload.endswith(b"\n"):
            chunk = connection.recv(1024)
            if not chunk:
                break
            payload.extend(chunk)
        return bytes(payload)

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2.0)
                received.append(receive_command(connection))
                connection.sendall(b"DRUCK,PACE5000,TEST,1.0\r")
                time.sleep(0.05)
                connection.sendall(b"\n")
                received.append(receive_command(connection))
                time.sleep(0.05)
                connection.sendall(b"BAR\r\n")
        except BaseException as exc:  # surfaced in the test thread
            server_errors.append(exc)
        finally:
            listener.close()

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    transport = TcpTransport(host, port, timeout=1.0)
    try:
        transport.connect()
        assert transport.query("*IDN?") == "DRUCK,PACE5000,TEST,1.0"
        assert transport.query(":UNIT1:PRES?") == "BAR"
    finally:
        transport.close()
        server.join(2.0)

    assert not server.is_alive()
    assert not server_errors
    assert received == [b"*IDN?\r\n", b":UNIT1:PRES?\r\n"]


def test_tcp_transport_binds_the_dedicated_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class FakeSocket:
        def setsockopt(self, level: int, option: int, value: int) -> None:
            calls.append(("setsockopt", level, option, value))

        def settimeout(self, timeout: float) -> None:
            calls.append(("settimeout", timeout))

        def shutdown(self, how: int) -> None:
            calls.append(("shutdown", how))

        def close(self) -> None:
            calls.append(("close",))

    def fake_create_connection(
        destination: tuple[str, int],
        timeout: float,
        source_address: tuple[str, int] | None = None,
    ) -> FakeSocket:
        calls.append(("connect", destination, timeout, source_address))
        return FakeSocket()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    transport = TcpTransport(
        "192.168.10.2", 5025, timeout=4.0, source_address="192.168.10.1"
    )
    transport.connect()
    transport.close()

    assert calls[0] == (
        "connect",
        ("192.168.10.2", 5025),
        4.0,
        ("192.168.10.1", 0),
    )
    assert ("setsockopt", socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) in calls


def test_windows_auto_network_waits_adds_route_and_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    responses = iter(
        [
            subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(
                    [
                        {
                            "Index": 3,
                            "Name": "PACE Ethernet",
                            "IPs": ["169.254.233.163"],
                            "HasGateway": False,
                        },
                        {
                            "Index": 16,
                            "Name": "Corporate",
                            "IPs": ["10.0.0.20"],
                            "HasGateway": True,
                        },
                    ]
                ),
                stderr="",
            ),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="created\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        ]
    )

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return next(responses)

    monkeypatch.setattr(network.subprocess, "run", fake_run)
    lease = network._configure_windows("192.168.10.1", 24)

    assert lease == network.NetworkLease(
        "3", True, "Windows", True, "192.168.10.1"
    )
    assert "-PolicyStore ActiveStore" in calls[1][-1]
    assert "AddressState -eq 'Preferred'" in calls[2][-1]
    assert "New-NetRoute" in calls[2][-1]

    network.restore_dedicated_adapter(lease)
    cleanup = calls[3][-1]
    assert "Remove-NetRoute" in cleanup
    assert "Remove-NetIPAddress" in cleanup


@pytest.mark.parametrize(
    ("elapsed", "drop", "expected"),
    [
        (600.0, 0.001, "no_leak"),
        (300.0, 0.0125, "slight_leak"),
        (180.0, 0.0225, "pressure_leak"),
        (180.0, 0.045, "significant_leak"),
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


@pytest.mark.parametrize(
    ("rate", "expected"),
    [(0.0025, "slight_leak"), (0.0075, "pressure_leak"), (0.015, "significant_leak")],
)
def test_leak_rate_is_immediate_but_warnings_wait_three_minutes(rate: float, expected: str) -> None:
    monitor = LeakMonitor(LeakThresholds())
    first = monitor.add(0.0, 50.0, True)
    assert first.observation_minutes == 0.0
    early = monitor.add(1.0, 50.0 - rate / 60.0, True)
    assert early.level == "assessing"
    assert early.observation_minutes == pytest.approx(1.0 / 60.0)
    assert early.rate_bar_min == pytest.approx(rate)
    before = monitor.add(179.999, 50.0 - rate * 179.999 / 60.0, True)
    assert before.level == "assessing"
    ready = monitor.add(180.0, 50.0 - rate * 3.0, True)
    assert ready.level == expected


def test_five_minute_average_excludes_old_loss_and_keeps_green_confirmation() -> None:
    monitor = LeakMonitor(LeakThresholds())
    monitor.add(0.0, 50.0, True)
    monitor.add(60.0, 49.0, True)
    for timestamp in range(120, 601, 60):
        result = monitor.add(float(timestamp), 49.0, True)
    assert result.rate_bar_min == 0.0
    assert result.observation_minutes == 5.0
    assert result.level == "no_leak"
    assert monitor.samples[0][0] == 300.0
    monitor.add(601.0, 49.0, False)
    assert monitor.add(602.0, 49.0, True).level == "assessing"
    assert monitor.add(603.0, 49.0, True).level == "assessing"
    assert monitor.add(782.0, 49.0, True).level == "no_leak"


def test_regression_interpolates_boundary_and_uses_irregular_timestamps() -> None:
    monitor = LeakMonitor(LeakThresholds())
    for timestamp, value in [(0.0, 50.0), (100.0, 49.0), (200.0, 49.0)]:
        monitor.add(timestamp, value, True)
    result = monitor.add(350.0, 48.0, True)
    # Fit (50,49.5), (100,49), (200,49), (350,48): loss slope = 19/70 bar/min.
    assert result.observation_minutes == 5.0
    assert result.rate_bar_min == pytest.approx(19.0 / 70.0)


def test_average_does_not_count_oscillating_noise_as_loss() -> None:
    monitor = LeakMonitor(LeakThresholds())
    monitor.add(0.0, 50.0, True)
    monitor.add(1.0, 49.0, True)
    result = monitor.add(3.0, 50.0, True)
    assert result.rate_bar_min == 0.0


def test_invalid_or_repeated_timestamps_do_not_corrupt_average() -> None:
    monitor = LeakMonitor(LeakThresholds())
    monitor.add(0.0, 50.0, True)
    for timestamp, value in [(float("nan"), 49.0), (1.0, float("nan")), (0.0, 40.0)]:
        assert monitor.add(timestamp, value, True).level == "assessing"
    result = monitor.add(60.0, 49.0, True)
    assert result.rate_bar_min == pytest.approx(1.0)


def test_ui_shows_early_loss_rate_and_autonomy() -> None:
    from types import SimpleNamespace

    from pace_controller.i18n import Translator
    from pace_controller.leak import LeakAssessment
    from pace_controller.ui import MainWindow

    displayed = []
    card = SimpleNamespace(set_level=lambda level, text: displayed.append((level, text)))
    window = SimpleNamespace(t=Translator("en"))
    MainWindow._apply_leak(
        window, card, LeakAssessment("assessing", 0.003, 1.0 / 60.0), 10.0
    )
    assert displayed[0][0] == "assessing"
    assert "LEAK" not in displayed[0][1]
    assert "0.180 bar/h" in displayed[0][1]
    assert "10.0 h" in displayed[0][1]
    assert "0.0 / 5 min" in displayed[0][1]
    MainWindow._apply_leak(window, card, LeakAssessment("paused_control"))
    assert "bar/h" not in displayed[1][1]
    assert "0.0 / 5 min" in displayed[1][1]
    MainWindow._apply_leak(window, card, LeakAssessment("no_leak", 0.0, 5.0))
    assert "5.0 / 5 min" in displayed[2][1]


def test_control_autonomy_uses_source_to_sample_pressure_headroom() -> None:
    assert control_autonomy_hours(50.0, 40.0, 1.0 / 60.0) == pytest.approx(10.0)


@pytest.mark.parametrize("window_minutes", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_averaging_windows_are_rejected(window_minutes: float) -> None:
    with pytest.raises(ValueError):
        LeakMonitor(LeakThresholds(), window_minutes)


def test_two_hour_window_is_independent_of_short_term_and_reset() -> None:
    short = LeakMonitor(LeakThresholds())
    long = LeakMonitor(LeakThresholds(), window_minutes=120.0)
    for timestamp in range(0, 7801, 60):
        pressure = 50.0 - 0.01 * min(timestamp / 60.0, 60.0)
        short_result = short.add(float(timestamp), pressure, True)
        long_result = long.add(float(timestamp), pressure, True)
    assert short_result.rate_bar_min == 0.0
    assert long_result.observation_minutes == 120.0
    assert long_result.rate_bar_min == pytest.approx(2227.0 / 590480.0)
    assert long.samples[0][0] == 600.0
    short_history = list(short.samples)
    long.reset()
    assert not long.samples
    assert list(short.samples) == short_history
    assert long.add(7801.0, 49.4, True).observation_minutes == 0.0


def test_long_term_buttons_start_and_reset_each_side_independently(monkeypatch, tmp_path) -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from pace_controller.models import AppSettings, Telemetry
    from pace_controller.service import PaceService
    from pace_controller.ui import MainWindow

    monkeypatch.setenv("PACE_CONTROLLER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(PaceService, "start", lambda self: None)
    monkeypatch.setattr(PaceService, "shutdown", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(PaceService(), AppSettings())
    try:
        window.on_connection_changed(True, {"key": "connected"})
        window.on_telemetry(Telemetry(timestamp=0.0, current_pressure_bar=10.0, positive_source_bar=50.0))
        window.sample_leak.long_term_button.click()
        assert window.long_term_active == {"sample"}
        assert "0.0 / 120 min" in window.sample_leak.long_term_value.text()
        assert not window.inlet_leak.long_term_reset.isEnabled()
        window.inlet_leak.long_term_button.click()
        for timestamp in (1.0, 61.0):
            window.on_telemetry(Telemetry(timestamp=timestamp, current_pressure_bar=10.0 - timestamp * 0.001, positive_source_bar=50.0 - timestamp * 0.002))
        assert "bar/h" in window.sample_leak.long_term_value.text()
        assert "bar/h" in window.inlet_leak.long_term_value.text()
        sample_history = list(window.long_term_monitors["sample"].samples)
        sample_assessment = window.long_term_assessments["sample"]
        window.sample_leak.long_term_stop.click()
        assert window.long_term_active == {"inlet"}
        assert window.long_term_stopped == {"sample"}
        assert "STOPPED" in window.sample_leak.long_term_value.text()
        assert window.sample_leak.long_term_button.isEnabled()
        assert not window.sample_leak.long_term_stop.isEnabled()
        window.on_telemetry(Telemetry(timestamp=61.5, current_pressure_bar=9.9, positive_source_bar=49.8))
        assert list(window.long_term_monitors["sample"].samples) == sample_history
        assert window.long_term_assessments["sample"] is sample_assessment
        assert window.long_term_assessments["inlet"].observation_minutes > sample_assessment.observation_minutes
        inlet_history = list(window.long_term_monitors["inlet"].samples)
        short_history = list(window.sample_monitor.samples)
        window.sample_leak.long_term_reset.click()
        assert not window.long_term_monitors["sample"].samples
        assert "sample" in window.long_term_active
        assert "sample" not in window.long_term_stopped
        assert "0.0 / 120 min" in window.sample_leak.long_term_value.text()
        assert window.long_term_assessments["sample"].observation_minutes == 0.0
        assert list(window.long_term_monitors["inlet"].samples) == inlet_history
        assert list(window.sample_monitor.samples) == short_history
        window.on_telemetry(Telemetry(timestamp=62.0, current_pressure_bar=9.9, positive_source_bar=49.8))
        assert len(window.long_term_monitors["sample"].samples) == 1
        window.on_telemetry(Telemetry(timestamp=63.0, current_pressure_bar=9.9, positive_source_bar=49.8, control=True, in_limits=False))
        assert not window.long_term_monitors["sample"].samples
        assert not window.long_term_monitors["inlet"].samples
        window.on_connection_changed(False, {"key": "disconnected"})
        assert not window.long_term_active
        assert not window.sample_leak.long_term_button.isEnabled()
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("window_minutes", [5.0, 120.0])
def test_linear_fit_recovers_loss_with_irregular_polling_and_epoch_timestamps(window_minutes):
    monitor = LeakMonitor(LeakThresholds(), window_minutes=window_minutes)
    origin = 1_800_000_000.0
    for offset in [0.0, 1.0, 9.0, 70.0, 150.0, 290.0, 350.0]:
        result = monitor.add(origin + offset, 50.0 - 0.003 * offset / 60.0, True)
    assert result.rate_bar_min == pytest.approx(0.003)
    assert result.observation_minutes == pytest.approx(min(350.0 / 60.0, window_minutes))


def test_regression_reduces_endpoint_noise_with_all_window_readings():
    monitor = LeakMonitor(LeakThresholds())
    for timestamp in range(301):
        pressure = 50.1 if timestamp == 0 else 49.9 if timestamp == 300 else 50.0
        result = monitor.add(float(timestamp), pressure, True)
    endpoint_rate = (50.1 - 49.9) / 5.0
    assert result.rate_bar_min < endpoint_rate / 10.0
    assert result.rate_bar_min == pytest.approx(1800.0 / 2272550.0)


def test_regression_uses_interior_readings_even_when_endpoints_match():
    monitor = LeakMonitor(LeakThresholds())
    for timestamp, pressure in [(0.0, 50.0), (60.0, 50.02), (120.0, 50.0), (180.0, 49.99), (240.0, 50.0)]:
        result = monitor.add(timestamp, pressure, True)
    assert result.rate_bar_min == pytest.approx(0.003)


@pytest.mark.parametrize("jump", [5.0, -5.0])
@pytest.mark.parametrize("window_minutes", [5.0, 120.0])
def test_pressure_steps_restart_window_from_new_baseline(jump, window_minutes):
    monitor = LeakMonitor(LeakThresholds(), window_minutes=window_minutes)
    for timestamp in range(21):
        monitor.add(float(timestamp), 50.0 - timestamp * 0.001, True)
    result = monitor.add(21.0, 49.979 + jump, True)
    assert result.history_reset
    assert result.observation_minutes == 0.0
    assert len(monitor.samples) == 1
    next_result = monitor.add(22.0, 49.978 + jump, True)
    assert not next_result.history_reset
    assert next_result.rate_bar_min == pytest.approx(0.06)


def test_large_continuous_drift_does_not_restart_the_regression():
    monitor = LeakMonitor(LeakThresholds())
    for timestamp in range(31):
        result = monitor.add(float(timestamp), 50.0 - 0.2 * timestamp, True)
        assert not result.history_reset
    assert result.observation_minutes == 0.5
    assert result.rate_bar_min == pytest.approx(12.0)


def test_cylinder_refill_resets_only_its_active_short_and_long_measurements(monkeypatch, tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from pace_controller.models import AppSettings, Telemetry
    from pace_controller.service import PaceService
    from pace_controller.ui import MainWindow
    monkeypatch.setenv("PACE_CONTROLLER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(PaceService, "start", lambda self: None)
    monkeypatch.setattr(PaceService, "shutdown", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(PaceService(), AppSettings())
    try:
        window.on_connection_changed(True, {"key": "connected"})
        window.start_long_term_leak("sample")
        window.start_long_term_leak("inlet")
        for timestamp in range(21):
            window.on_telemetry(Telemetry(timestamp=float(timestamp), current_pressure_bar=10.0, positive_source_bar=50.0))
        window.on_telemetry(Telemetry(timestamp=21.0, current_pressure_bar=10.0, positive_source_bar=60.0))
        assert window.inlet_assessment.history_reset
        assert window.inlet_assessment.observation_minutes == 0.0
        assert window.long_term_assessments["inlet"].observation_minutes == 0.0
        assert window.sample_assessment.observation_minutes == pytest.approx(21.0 / 60.0)
        assert window.long_term_assessments["sample"].observation_minutes == pytest.approx(21.0 / 60.0)
        assert "0.0 / 120 min" in window.inlet_leak.long_term_value.text()
        assert window.long_term_active == {"sample", "inlet"}
        window.on_telemetry(Telemetry(timestamp=22.0, current_pressure_bar=11.0, positive_source_bar=60.0))
        assert window.sample_assessment.history_reset
        assert window.long_term_assessments["sample"].observation_minutes == 0.0
        assert window.inlet_assessment.observation_minutes > 0.0
        assert window.long_term_assessments["inlet"].observation_minutes > 0.0
    finally:
        window.close()
        app.processEvents()

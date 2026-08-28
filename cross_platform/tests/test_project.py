from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_and_documentation_files_exist() -> None:
    expected = [
        "README.md",
        "docs/PACE_Controller_Manual.md",
        "docs/PACE_Controller_Manual.pdf",
        "docs/pace_controller_gui.png",
        "requirements.txt",
        "pyproject.toml",
        "src/pace_controller/main.py",
        "src/pace_controller/service.py",
        "src/pace_controller/transports.py",
        "pace_controller_launcher.py",
        "scripts/build_windows.ps1",
        "scripts/build_linux.sh",
    ]
    for relative in expected:
        assert (ROOT / relative).is_file(), relative


def test_required_cross_platform_features_are_present() -> None:
    transport = (ROOT / "src/pace_controller/transports.py").read_text(encoding="utf-8")
    service = (ROOT / "src/pace_controller/service.py").read_text(encoding="utf-8")
    ui = (ROOT / "src/pace_controller/ui.py").read_text(encoding="utf-8")
    assert "class TcpTransport" in transport
    assert "class SerialTransport" in transport
    assert "class SimulatorTransport" in transport
    assert "minimum_source_margin_bar: float = 2.0" in service
    assert "source_margin_rearm_bar: float = 2.2" in service
    assert 'f"{value:.3f} {unit}"' in ui
    assert "LockButton" in ui


def test_frozen_legacy_hash_is_declared() -> None:
    assert (ROOT / "LEGACY_SHA256.txt").read_text(encoding="utf-8").strip().startswith(
        "aa6ffe5431dfab7d2ea998f9b59e8ac5163b0e3478e84a3c15e2e826fb356b8e"
    )

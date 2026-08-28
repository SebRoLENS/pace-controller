#!/usr/bin/env python3
"""Dependency-free repository consistency checks."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "PACE_Controller.ps1").read_text(encoding="utf-8")
        match = re.search(
            r'(?m)^\$script:Version\s*=\s*"(\d+\.\d+\.\d+)"$', cls.script
        )
        if not match:
            raise AssertionError("PowerShell version not found")
        cls.version = match.group(1)

    def test_release_metadata_versions_match(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        manual = (ROOT / "docs" / "PACE_Controller_Manual.md").read_text(
            encoding="utf-8"
        )
        citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        self.assertIn(f"Current public version: **{self.version}**", readme)
        self.assertIn(f"version **{self.version}**", manual)
        self.assertRegex(citation, rf'(?m)^version: "{re.escape(self.version)}"$')

    def test_critical_scpi_and_protections_are_present(self) -> None:
        required = [
            ":SENS${module}:PRES:CONT?",
            ":SOUR${module}:PRES:COMP1?",
            ":SOUR${module}:PRES:SLEW",
            ":OUTP${module}:STAT OFF",
            "MinimumSupplyMarginBar = 2.0",
            "Invoke-SupplyMarginInterlock",
            "Update-LeakMonitoring",
            'SignificantLeak = "WARNING: SIGNIFICANT PRESSURE LEAK"',
            'SignificantLeak = "ATTENZIONE, PERDITA SIGNIFICATIVA PRESSIONE"',
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.script)

    def test_default_leak_thresholds_are_ordered(self) -> None:
        def value(name: str) -> float:
            match = re.search(
                rf"(?m)^\$script:{name}\s*=\s*([0-9.]+)$", self.script
            )
            self.assertIsNotNone(match, name)
            return float(match.group(1))

        drop = value("LeakReferenceDropBar")
        green = value("LeakGreenMinutes")
        yellow = value("LeakYellowMinutes")
        orange = value("LeakOrangeMinutes")
        self.assertGreater(drop, 0)
        self.assertGreater(green, yellow)
        self.assertGreater(yellow, orange)
        self.assertGreater(orange, 0)
        self.assertAlmostEqual(drop / green, 0.0005)
        self.assertAlmostEqual(drop / yellow, 0.001)
        self.assertAlmostEqual(drop / orange, 0.005)

    def test_example_routine(self) -> None:
        steps = json.loads(
            (ROOT / "examples" / "routine_esempio.json").read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(len(steps), 1)
        for step in steps:
            self.assertIn("Target", step)
            self.assertGreater(float(step["Slew"]), 0)
            self.assertGreaterEqual(float(step["Dwell"]), 0)

    def test_localization_lock_and_screenshot_mode(self) -> None:
        required = [
            '[string]$Language = "en"',
            '[ValidateSet("en", "it")]',
            '$script:AdvancedParametersUnlocked = $false',
            'DangerWarning = "DANGER AREA:',
            'DangerWarning = "AREA PERICOLOSA:',
            "Update-AdvancedParameterLockUi",
            "Export-InterfaceScreenshot",
            "$form.DrawToBitmap",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.script)

    def test_telemetry_cards_wrap_and_use_three_decimals(self) -> None:
        required = [
            "$metricsFlow.WrapContents = $true",
            "$metricsFlow.AutoScroll = $false",
            '$pressure.ToString("0.000", $script:Culture)',
            '$target.ToString("0.000", $script:Culture)',
            '$actualSlew.ToString("0.000", $script:Culture)',
            '$effort.ToString("0.000", $script:Culture)',
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.script)

    def test_release_files_exist(self) -> None:
        expected = [
            ".github/workflows/ci.yml",
            ".github/workflows/automatic-release.yml",
            ".github/scripts/prepare_release.py",
            "docs/pace_controller_gui.png",
            "LICENSE",
            "CITATION.cff",
        ]
        for relative in expected:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)

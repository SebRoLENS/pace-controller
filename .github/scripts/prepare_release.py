#!/usr/bin/env python3
"""Prepare a consistent semantic-versioned PACE Controller release."""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "PACE_Controller.ps1"
README = ROOT / "README.md"
MANUAL = ROOT / "docs" / "PACE_Controller_Manual.md"
CITATION = ROOT / "CITATION.cff"
CHANGELOG = ROOT / "CHANGELOG.md"

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
SCRIPT_VERSION_RE = re.compile(
    r'(?m)^\$script:Version\s*=\s*"(?P<version>\d+\.\d+\.\d+)"\s*$'
)


def version_tuple(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(version)
    if not match:
        raise SystemExit(f"Unsupported semantic version: {version}")
    return tuple(map(int, match.groups()))


def read_current_version() -> str:
    match = SCRIPT_VERSION_RE.search(SCRIPT.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("Could not find $script:Version in PACE_Controller.ps1")
    return match.group("version")


def tagged_versions() -> list[str]:
    output = subprocess.check_output(
        ["git", "tag", "--list", "v*"], cwd=ROOT, text=True
    )
    versions: list[str] = []
    for raw in output.splitlines():
        candidate = raw.removeprefix("v")
        if SEMVER_RE.fullmatch(candidate):
            versions.append(candidate)
    return versions


def choose_version(current: str) -> str:
    tags = tagged_versions()
    if current not in tags:
        if tags and version_tuple(current) <= max(map(version_tuple, tags)):
            raise SystemExit(
                f"Untagged source version {current} is not newer than the latest release"
            )
        return current
    major, minor, patch = version_tuple(current)
    return f"{major}.{minor}.{patch + 1}"


def update_script(old: str, new: str) -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    text = SCRIPT_VERSION_RE.sub(f'$script:Version = "{new}"', text, count=1)
    SCRIPT.write_text(text, encoding="utf-8")


def update_readme(new: str) -> None:
    text = README.read_text(encoding="utf-8")
    pattern = re.compile(r"(?m)^Current public version: \*\*\d+\.\d+\.\d+\*\*$")
    if not pattern.search(text):
        raise SystemExit("README current-version field not found")
    text = pattern.sub(f"Current public version: **{new}**", text, count=1)
    README.write_text(text, encoding="utf-8")


def update_manual(new: str) -> None:
    text = MANUAL.read_text(encoding="utf-8")
    text, date_count = re.subn(
        r'(?m)^date: "Version \d+\.\d+\.\d+ - \d{4}"$',
        f'date: "Version {new} - {dt.date.today().year}"',
        text,
        count=1,
    )
    text, body_count = re.subn(
        r"User and technical manual for version \*\*\d+\.\d+\.\d+\*\*\.",
        f"User and technical manual for version **{new}**.",
        text,
        count=1,
    )
    if date_count != 1 or body_count != 1:
        raise SystemExit("Manual version fields not found")
    MANUAL.write_text(text, encoding="utf-8")


def update_citation(new: str) -> None:
    text = CITATION.read_text(encoding="utf-8")
    text, version_count = re.subn(
        r'(?m)^version: ".*"$', f'version: "{new}"', text, count=1
    )
    text, url_count = re.subn(
        r'(?m)^url: ".*"$',
        f'url: "https://github.com/SebRoLENS/pace-controller/releases/tag/v{new}"',
        text,
        count=1,
    )
    text, date_count = re.subn(
        r"(?m)^date-released: .*$",
        f"date-released: {dt.date.today().isoformat()}",
        text,
        count=1,
    )
    if version_count != 1 or url_count != 1 or date_count != 1:
        raise SystemExit("CITATION.cff release fields not found")
    CITATION.write_text(text, encoding="utf-8")


def update_changelog(old: str, new: str) -> None:
    if old == new:
        return
    text = CHANGELOG.read_text(encoding="utf-8")
    if re.search(rf"(?m)^## \[{re.escape(new)}\]", text):
        return
    insertion = (
        f"## [{new}] - {dt.date.today().isoformat()}\n\n"
        "- Automated validated maintenance release.\n\n"
    )
    marker = "All notable changes are documented here. The project follows semantic versioning.\n\n"
    if marker not in text:
        raise SystemExit("CHANGELOG insertion point not found")
    CHANGELOG.write_text(text.replace(marker, marker + insertion, 1), encoding="utf-8")


def main() -> None:
    old = read_current_version()
    new = choose_version(old)
    update_script(old, new)
    update_readme(new)
    update_manual(new)
    update_citation(new)
    update_changelog(old, new)
    print(new)


if __name__ == "__main__":
    main()

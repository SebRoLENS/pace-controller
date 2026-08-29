#!/usr/bin/env python3
"""Prepare one consistent semantic-versioned cross-platform release."""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CROSS = ROOT / "cross_platform"
INIT = CROSS / "src" / "pace_controller" / "__init__.py"
PYPROJECT = CROSS / "pyproject.toml"
CROSS_README = CROSS / "README.md"
ROOT_README = ROOT / "README.md"
MANUAL = CROSS / "docs" / "PACE_Controller_Manual.md"
CITATION = ROOT / "CITATION.cff"
CHANGELOG = ROOT / "CHANGELOG.md"
LEGACY = ROOT / "PACE_Controller.ps1"
LEGACY_HASH = "aa6ffe5431dfab7d2ea998f9b59e8ac5163b0e3478e84a3c15e2e826fb356b8e"
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
VERSION_BADGE_RE = re.compile(
    r"^\[!\[(?:Latest release|Version)\]\([^)]+\)\]\([^)]+\)[ \t]*$", re.M
)
DOI_BADGE_RE = re.compile(r"^\[!\[DOI\]\([^)]+\)\]\([^)]+\)[ \t]*$", re.M)
VERSION_BADGE = (
    "[![Version](https://img.shields.io/github/v/release/SebRoLENS/pace-controller)]"
    "(https://github.com/SebRoLENS/pace-controller/releases/latest)"
)
DOI_PENDING_BADGE = (
    "[![DOI](https://img.shields.io/badge/DOI-pending-lightgrey)]"
    "(https://github.com/SebRoLENS/pace-controller/releases/latest)"
)


def version_tuple(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(version)
    if not match:
        raise SystemExit(f"Unsupported semantic version: {version}")
    return tuple(map(int, match.groups()))


def current_version() -> str:
    match = re.search(
        r'(?m)^__version__\s*=\s*"(?P<version>\d+\.\d+\.\d+)"$',
        INIT.read_text(encoding="utf-8"),
    )
    if not match:
        raise SystemExit("Could not find cross-platform __version__")
    return match.group("version")


def tagged_versions() -> list[str]:
    output = subprocess.check_output(["git", "tag", "--list", "v*"], cwd=ROOT, text=True)
    return [tag[1:] for tag in output.splitlines() if SEMVER_RE.fullmatch(tag[1:])]


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


def replace_one(path: Path, pattern: str, replacement: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Could not update {label} in {path.relative_to(ROOT)}")
    path.write_text(updated, encoding="utf-8")


def assert_legacy_unchanged() -> None:
    import hashlib

    canonical = LEGACY.read_bytes().replace(b"\r\n", b"\n")
    actual = hashlib.sha256(canonical).hexdigest()
    if actual != LEGACY_HASH:
        raise SystemExit(
            "PACE_Controller.ps1 changed unexpectedly; the validated v0.3.1 source is frozen"
        )


def replace_section(text: str, heading: str, next_heading: str, body: str) -> str:
    pattern = re.compile(
        rf"(?ms)^{re.escape(heading)}\n.*?(?=^{re.escape(next_heading)}\n)"
    )
    if not pattern.search(text):
        raise SystemExit(f"Could not find README section {heading!r}")
    return pattern.sub(body.rstrip() + "\n\n", text, count=1)


def update_pending_doi_metadata(version: str) -> None:
    text = ROOT_README.read_text(encoding="utf-8")
    if not VERSION_BADGE_RE.search(text):
        raise SystemExit("Could not find README version badge")
    text = VERSION_BADGE_RE.sub(VERSION_BADGE, text, count=1)
    if DOI_BADGE_RE.search(text):
        text = DOI_BADGE_RE.sub(DOI_PENDING_BADGE, text, count=1)
    else:
        text = text.replace(VERSION_BADGE, VERSION_BADGE + "\n" + DOI_PENDING_BADGE, 1)
    citation = f"""## Citation

If PACE Controller contributes to published work, cite the exact version used.
GitHub also provides a **Cite this repository** entry from [`CITATION.cff`](CITATION.cff).

Version **{version}** will be archived by the Zenodo GitHub integration. The
version DOI will then be inserted here automatically.

> Romi, S. (2026). *PACE Controller* (Version {version}) [Computer software].
> GitHub. https://github.com/SebRoLENS/pace-controller/releases/tag/v{version}
"""
    ROOT_README.write_text(
        replace_section(text, "## Citation", "## License and independence", citation),
        encoding="utf-8",
    )


def update_version(new: str) -> None:
    replace_one(INIT, r'^__version__\s*=\s*"[^"]+"$', f'__version__ = "{new}"', "package version")
    replace_one(PYPROJECT, r'^version\s*=\s*"[^"]+"$', f'version = "{new}"', "project version")
    replace_one(CROSS_README, r'^Current version: \*\*[^*]+\*\*$', f'Current version: **{new}**', "README version")
    replace_one(ROOT_README, r'^Current public version: \*\*[^*]+\*\*$', f'Current public version: **{new}**', "root README version")
    replace_one(MANUAL, r'^date: "Version [^"]+"$', f'date: "Version {new} - {dt.date.today().year}"', "manual date")
    replace_one(MANUAL, r'^User and technical manual for version \*\*[^*]+\*\*\.$', f'User and technical manual for version **{new}**.', "manual body version")
    replace_one(CITATION, r'^version: ".*"$', f'version: "{new}"', "citation version")
    replace_one(CITATION, r'^url: ".*"$', f'url: "https://github.com/SebRoLENS/pace-controller/releases/tag/v{new}"', "citation URL")
    replace_one(CITATION, r'^date-released: .*$', f'date-released: {dt.date.today().isoformat()}', "citation date")
    cff = re.sub(r"^doi:\s*.*\n", "", CITATION.read_text(encoding="utf-8"), flags=re.M)
    CITATION.write_text(cff, encoding="utf-8")
    update_pending_doi_metadata(new)


def update_changelog(new: str) -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    if re.search(rf"(?m)^## \[{re.escape(new)}\]", text):
        return
    marker = "All notable changes are documented here. The project follows semantic versioning.\n\n"
    if marker not in text:
        raise SystemExit("CHANGELOG insertion point not found")
    entry = (
        f"## [{new}] - {dt.date.today().isoformat()}\n\n"
        "- Automated validated cross-platform maintenance release.\n\n"
    )
    CHANGELOG.write_text(text.replace(marker, marker + entry, 1), encoding="utf-8")


def main() -> None:
    assert_legacy_unchanged()
    new = choose_version(current_version())
    update_version(new)
    update_changelog(new)
    assert_legacy_unchanged()
    print(new)


if __name__ == "__main__":
    main()

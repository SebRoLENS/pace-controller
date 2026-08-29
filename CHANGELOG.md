# Changelog

All notable changes are documented here. The project follows semantic versioning.

## [1.0.1] - 2026-08-29

- Fixed the Ethernet connection regression introduced in v1.0.0: TCP/SCPI
  commands now end in CRLF, preserving the validated v0.3.1 behaviour and the
  LF terminator required by the Druck K0472 manual.
- Ignored an empty line-feed fragment when a CRLF instrument reply is split
  across TCP packets.
- Added a loopback TCP regression test for the complete connection handshake.

## [1.0.0] - 2026-08-28

- Added a separate Python/PySide6 implementation for Windows and Linux.
- Added interchangeable Ethernet TCP, RS-232, and offline simulator transports.
- Preserved all manual, indenting, routine, leak-monitoring, logging, and safety features.
- Added automatic Windows and Linux packaging, real Qt screenshots, tests, and cross-platform documentation.
- Kept the validated PowerShell/WinForms v0.3.1 implementation unchanged.

## [0.3.1] - 2026-08-28

- Kept the language selector visible at the minimum supported window width.
- Captured the complete default-size interface in automated screenshots.
- Arranged all telemetry cards on two visible rows and standardized displayed numeric values to three decimal places.

## [0.3.0] - 2026-08-28

- Added complete English-default localization with persistent Italian selection.
- Locked pressurization parameters behind a padlock and explicit danger-area confirmation.
- Added a hardware-free screenshot mode and automated real WinForms screenshot generation.
- Updated README and manual for the bilingual interface and parameter lock.

## [0.2.1] - 2026-08-28

- Automated validated maintenance release.

## [0.2.0] - 2026-08-28

- Added persistent sample-side and positive-inlet leak indicators.
- Added configurable leak thresholds and persistent settings.
- Added source-margin interlock below 2.0 bar with 2.2 bar re-arm threshold.
- Added fail-safe MEASURE attempts when critical telemetry is lost during CONTROL.
- Improved navigation tabs and programmable-routine layout.
- Removed the intrusive pressure-history graph while retaining CSV telemetry.
- Added automated Windows executable builds and release infrastructure.

## [0.1.0] - 2026-08-28

- Initial Ethernet controller with manual pressure control, indenting, programmable routines, telemetry, and basic software warnings.

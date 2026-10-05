# Changelog

All notable changes are documented here. The project follows semantic versioning.

## [1.1.2] - 2026-10-05

- Automated validated cross-platform maintenance release.

## [Unreleased]

- Add independent Stop buttons for long term measurements, keeping the last result visible until a new measurement starts.
- Display available short term averaging history out of five minutes and long term history from 0 / 120 min.
- Estimate short term and long term pressure-loss rates using linear least-squares fits over all readings in their rolling windows, instead of endpoint differences.
- Restart the affected side's short term and active long term regression windows after large pressure steps, such as cylinder refills, while retaining continuous drift.

## [1.1.1] - 2026-10-05

- Automated validated cross-platform maintenance release.

## [Unreleased]

- Add independent manual long term leak measurements for cell and cylinder, with two-hour moving averages and separate Reset buttons.

- Show pressure-loss rates and leak warnings from the first two valid readings, without the three-minute delay.
- Use a time-weighted five-minute moving average, including available startup history, for both loss indicators and CONTROL autonomy.
- Keep green confirmation tied to uninterrupted monitoring time, independently of the averaging window.

## [1.1.0] - 2026-09-07

- Stabilized sample-side and cylinder-side pressure-loss estimates by requiring at least three minutes of regression history before displaying a rate.
- Displayed pressure-loss rates in bar/h.
- Added cylinder-pressure monitoring during steady CONTROL and estimated remaining CONTROL autonomy from source pressure, sample pressure, and measured cylinder loss rate.

## [1.0.3] - 2026-08-29

- Automated validated cross-platform maintenance release.

## [1.0.2] - 2026-08-29

- Automated validated cross-platform maintenance release.

## [1.0.1] - 2026-08-29

- Fixed the Ethernet connection regression introduced in v1.0.0: TCP/SCPI
  commands now end in CRLF, preserving the validated v0.3.1 behaviour and the
  LF terminator required by the Druck K0472 manual.
- Ignored an empty line-feed fragment when a CRLF instrument reply is split
  across TCP packets.
- Added a loopback TCP regression test for the complete connection handshake.
- Added a bilingual Help menu, author/affiliation acknowledgements, and a
  direct GitHub issue-reporting action with robust frozen-Linux link opening.
- Added automatic post-release Zenodo DOI discovery and metadata synchronisation.

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

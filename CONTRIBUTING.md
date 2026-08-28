# Contributing

Bug reports, validation results, documentation improvements, and pull requests are welcome.

## Before reporting a control problem

Reproduce it only on a safe, unloaded, low-pressure setup. Include:

- PACE 5000 or 6000 and selected module;
- firmware version, if known;
- Windows version;
- the exact action or routine;
- expected and observed behaviour;
- `PACE_controller_log.txt` with sensitive local information removed;
- whether the same command works from the PACE front panel.

Never publish credentials, internal network details, or proprietary sample information.

## Pull requests

1. Keep the application compatible with Windows PowerShell 5.1.
2. Preserve the default safe behaviour: MEASURE after operations unless CONTROL was explicitly requested.
3. Do not weaken adapter-selection, target-range, slew, pressure-jump, source-margin, timeout, or communication protections.
4. Update the manual and tests when behaviour changes.
5. Run `python tests/test_project.py` and parse the PowerShell source before opening a pull request.

Hardware-dependent changes should describe the instrument and low-pressure validation performed.

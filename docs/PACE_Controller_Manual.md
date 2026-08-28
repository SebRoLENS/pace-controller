---
title: "PACE Controller - User and Technical Manual"
author: "S. Romi"
date: "Version 0.2.0 - 2026"
geometry: margin=2.2cm
colorlinks: true
lang: en
---

# PACE Controller

User and technical manual for version **0.2.0**.

## 1. Scope

PACE Controller is a Windows graphical application for classic Druck PACE 5000 and PACE 6000 pressure controllers connected through a direct Ethernet link. It is intended for supervised laboratory operation, initial automation, pressure routines, and telemetry acquisition.

The application communicates directly through a TCP socket using SCPI commands. It does not require NI-VISA, Druck USB drivers, Python, LabVIEW, or access to the Internet on the instrument PC.

## 2. Safety notice

This software can command real pressurization, venting, and changes between CONTROL and MEASURE. Before use:

- verify the pressure rating of the sample, cell, tubing, fittings, valves, and seals;
- verify SUPPLY, OUTLET, VENT, and reference connections;
- configure appropriate limits in the PACE itself;
- keep the PACE front panel and the physical source shutoff accessible;
- test every workflow without a valuable sample and at low pressure;
- never rely on this program as the only pressure-protection layer.

The software protections described below depend on valid telemetry and a working network connection. They are neither safety-rated nor deterministic hardware interlocks.

## 3. Supported configuration

### 3.1 Instrument

- Druck PACE 5000, classic model without the E suffix;
- Druck PACE 6000, classic model without the E suffix;
- control module 1 or 2, selected from the interface.

### 3.2 Windows PC

- Windows 10 or Windows 11;
- Ethernet adapter dedicated to the PACE;
- administrator privileges, required only to configure the selected adapter temporarily;
- Windows PowerShell 5.1 for source execution, or the packaged `.exe`.

### 3.3 PACE Ethernet parameters

Configure the instrument with:

| Parameter | Value |
| --- | --- |
| Address | Static |
| IP address | `192.168.10.2` |
| Subnet mask | `255.255.255.0` |
| Gateway | empty or `0.0.0.0` |
| Access control | Open |
| SCPI TCP port | `5025` |

Connect the PC and the PACE directly with an Ethernet cable. Modern adapters normally support automatic crossover.

## 4. Automatic network configuration

At connection time, the program first tries the configured PACE address. If it is unreachable, it searches for exactly one safe Ethernet adapter satisfying all of these conditions:

- physical wired Ethernet;
- link state Up;
- no default gateway;
- no existing non-link-local IPv4 address.

Only that adapter receives the temporary address `192.168.10.1/24`. Adapters carrying another instrument network, a corporate network, Wi-Fi, or a default route are rejected. If zero or more than one safe candidate is found, no adapter is modified and the program reports the ambiguity.

The temporary address and route are removed at shutdown, and the original DHCP state is restored. An abnormal process termination can prevent restoration; in that case reset the adapter from Windows network settings or with `netsh`/PowerShell.

## 5. Starting the application

### 5.1 Standalone executable

Download `PACE-Controller-vX.Y.Z-Windows-x86_64.exe`, place it in a writable folder, and run it. The UAC request is expected because the program must configure the dedicated Ethernet adapter.

The executable is currently unsigned. Windows SmartScreen may therefore ask for confirmation.

### 5.2 Source version

Extract the complete source archive and double-click `Avvia_PACE_Controller.bat`. Do not move the launcher away from `PACE_Controller.ps1`.

## 6. Main display

The top section remains visible from every page and displays:

- current pressure;
- pressure target;
- positive and negative source pressure;
- measured slew rate;
- valve effort;
- CONTROL or MEASURE state;
- in-limits state;
- positive-source margin.

Immediately below it, two large panels display sample-side and inlet-side leak status. The operating pages are MANUALE, INDENTING, ROUTINE, IMPOSTAZIONI, and LOG.

## 7. Manual control

### 7.1 New target

Enter:

- target pressure in bar;
- slew rate in bar/s;
- Linear or Maximum slew mode;
- whether CONTROL should be maintained after the target reaches in-limits.

The keep-CONTROL option is off by default. When it is off, the program requests MEASURE as soon as the target is confirmed in-limits.

### 7.2 Pressurization parameters

- **Control mode:** Active, Passive, or Gauge, corresponding to the selected PACE output mode.
- **Overshoot:** enables or disables the PACE overshoot option.
- **In-limits tolerance:** percentage of full scale used by the controller to declare target reached.
- **In-limits time:** required stable duration before target confirmation.
- **Vent rate:** controlled vent rate in bar/s.

The VENT command always requires confirmation.

## 8. Indenting cycle

The indenting page performs a fixed sequence:

1. set the requested slew;
2. enter CONTROL and reach the requested pressure;
3. wait until the PACE reports in-limits;
4. maintain the target for 120 seconds;
5. command zero bar with the same slew;
6. wait for in-limits at zero;
7. request MEASURE.

Stopping the cycle requests MEASURE immediately.

## 9. Programmable routines

Each table row contains:

- target pressure in bar;
- slew rate in bar/s;
- dwell time at target in seconds;
- an optional note.

Rows are executed from top to bottom. A step begins its dwell only after the PACE reports in-limits. Routines can be saved and loaded as JSON. See `examples/routine_esempio.json`.

At completion the program requests MEASURE unless **Mantieni CONTROL alla fine** was explicitly selected. Timeouts or user interruption request MEASURE.

## 10. Source-margin interlock

The source margin is defined as:

\[
P_{margin}=P_{source,+}-P_{current}.
\]

When the PACE is in CONTROL and the margin becomes lower than 2.0 bar, the software:

1. cancels the active manual sequence, indenting cycle, or routine;
2. sends the command for MEASURE;
3. displays a warning;
4. records the event in the log.

Targets that would leave less than 2.0 bar are rejected before CONTROL begins. After an intervention, CONTROL remains locked until the measured margin reaches at least 2.2 bar. This hysteresis prevents repeated operation at the boundary.

If required source telemetry fails during CONTROL, the software attempts MEASURE as a fail-safe action. If confirmation is impossible, the operator must use the front panel immediately.

## 11. Leak monitoring

### 11.1 Signals

Two independent trends are calculated:

- **sample side:** current PACE controlled pressure;
- **inlet side:** positive source pressure reported by `COMP1`.

Only decreases are interpreted as losses. Increases produce a zero loss rate.

### 11.2 Operating condition

The rolling histories are collected only in MEASURE. During CONTROL or an active automation they are cleared and both panels show **VALUTAZIONE IN PAUSA (CONTROL)**. This avoids treating an intentional pressure change or source consumption as a leak.

After returning to MEASURE the collection starts again. Until sufficient history is available, the panel shows **IN VALUTAZIONE** rather than claiming that no leak exists.

### 11.3 Calculation

The program fits pressure versus time by linear least squares over the rolling history. The negative fitted slope is the estimated loss rate. Positive slopes are clipped to zero.

With the defaults, the continuous classes are:

| Display | Colour | Equivalent fitted loss rate |
| --- | --- | --- |
| `NO PERDITA` | green | <=0.0005 bar/min |
| `ATTENZIONE, lieve perdita` | yellow | >0.0005 and <=0.001 bar/min |
| `ATTENZIONE, perdita pressione` | orange | >0.001 and <=0.005 bar/min |
| `ATTENZIONE, PERDITA SIGNIFICATIVA PRESSIONE` | red | >0.005 bar/min |

Green is confirmed only after 10 minutes. Yellow requires at least 5 minutes and orange at least 1 minute. A red state can be issued earlier when the reference loss is already exceeded inside the orange interval or a sufficiently clear fast slope is observed.

### 11.4 Custom thresholds

The IMPOSTAZIONI page allows editing:

- reference pressure drop, default 0.005 bar;
- green time, default 10 min;
- yellow time, default 5 min;
- orange time, default 1 min.

The required ordering is `green time > yellow time > orange time > 0`. The preview shows the equivalent continuous rates. Settings are saved in `PACE_controller_settings.json` and loaded at the next start.

### 11.5 Interpretation limitations

A pressure trend does not uniquely identify a physical leak. Temperature changes, gas thermalization, specimen relaxation, regulator hysteresis, pressure-medium behaviour, and sensor noise can produce similar signals. Confirm alarms using the appropriate laboratory leak-test procedure.

## 12. Additional software warnings

- An upward target change of at least 10 bar requires explicit confirmation.
- A slew above 0.5 bar/s or Maximum mode requires explicit confirmation before any target change.
- Targets outside the range reported by the selected PACE module are blocked.
- Automatic operations have a calculated timeout; timeout requests MEASURE.

## 13. Generated files

The application directory can contain:

- `PACE_controller_data.csv`: timestamped telemetry;
- `PACE_controller_log.txt`: commands, responses, state changes, errors, and interlock events;
- `PACE_controller_settings.json`: persistent leak thresholds;
- user-selected JSON routine files.

## 14. Troubleshooting

### PACE cannot be reached

Verify the PACE static address, cable, Ethernet LEDs, TCP port 5025, and Access Control = Open. Confirm that no other PC adapter uses `192.168.10.0/24`.

### No safe Ethernet adapter is found

Disconnect other unused wired adapters or manually identify the dedicated adapter. The program intentionally refuses ambiguous automatic configuration.

### More than one safe adapter is found

Disable the unrelated unused Ethernet adapter temporarily. No adapter is modified while the choice remains ambiguous.

### The application reports communication loss during CONTROL

Use the PACE front panel immediately, select MEASURE if necessary, verify the physical system, then check the cable and reconnect. The software cannot guarantee delivery of MEASURE after loss of communication.

### Leak status remains in evaluation

Confirm that the PACE is in MEASURE. Green requires the complete configured green interval. Any return to CONTROL, venting, module change, settings change, or reconnection restarts the history.

## 15. SCPI implementation

The application uses commands described in the Druck PACE SCPI Remote Communications Manual K0472, including identification, pressure measurement, target, slew, output state/mode, in-limits, source compensation pressure, effort, units, vent, system error, and local-mode commands.

Commands and telemetry are normalized to bar and bar/s after connection.

## 16. Release and reproducibility

The repository contains the complete PowerShell source and GitHub Actions workflows. A source change on `main` triggers validation, semantic version preparation, manual generation, a Windows build using PS2EXE, source packaging, SHA-256 checksums, tagging, and publication of a GitHub release.

The `.exe` is a convenience wrapper around the reviewed PowerShell source. It remains unsigned and must be treated according to local IT policy.

## 17. License and project status

PACE Controller is released under the MIT License. It is independent software and is not an official Druck or Baker Hughes product.

Development used AI-assisted programming. Validate the application on a safe low-pressure setup and report unexpected behaviour with the relevant log, PACE model/module, firmware version, and exact reproduction steps.

# PACE Controller

Cross-platform graphical controller for classic Druck **PACE 5000** and **PACE 6000** instruments connected through Ethernet or RS-232.

Current version: **1.0.0**

> [!CAUTION]
> This application sends real pressure-control and vent commands. It is not a certified safety system and does not replace pressure-relief devices, hardware interlocks, instrument limits, laboratory procedures, or direct operator supervision.

## Download

Download the latest release from [GitHub Releases](https://github.com/SebRoLENS/pace-controller/releases/latest).

- `PACE-Controller-vX.Y.Z-Windows-x86_64.exe`: standalone Windows executable;
- `PACE-Controller-vX.Y.Z-Linux-x86_64.tar.gz`: standalone Linux executable;
- `PACE-Controller-vX.Y.Z-source.zip`: complete source and documentation;
- `PACE_Controller_Manual.pdf`: versioned user and technical manual;
- `SHA256SUMS.txt`: file-integrity hashes.

The Windows executable is unsigned, so SmartScreen may request confirmation. The Linux archive is also distributed without a commercial signing certificate. Neither package needs Python; Linux still requires the normal graphical-system libraries supplied by a modern desktop distribution. Build automation and complete source code are public for inspection.

## Interface

![Real PACE Controller interface](docs/pace_controller_gui.png)

This image is generated automatically by the real PySide6 interface in offline simulator mode. No instrument commands are sent while producing it.

## Supported connections

| Connection | Windows | Linux | Notes |
|---|---:|---:|---|
| Ethernet TCP/SCPI | Yes | Yes | Default `192.168.10.2:5025` |
| Native RS-232 | Yes | Yes | `COMx` or `/dev/ttySx` |
| USB-to-RS-232 | Yes | Yes | Appears as a normal serial port |
| Offline simulator | Yes | Yes | Hardware-free training and screenshots |
| IEEE-488/GPIB | Not yet | Not yet | Planned optional transport |
| Direct USB/VISA | No | No | The classic PACE requires proprietary/VISA components |

Ethernet and RS-232 use the same SCPI control engine and the same safety checks. No NI-VISA, Druck USB driver, LabVIEW, or Internet connection is required at runtime.

## Main features

- English interface by default, with persistent Italian selection.
- Nine telemetry cards arranged on two visible rows, using three decimal places.
- Current pressure, target, positive and negative source, measured slew, valve effort, CONTROL/MEASURE state, in-limits state, and source margin.
- Manual target control with Linear or Maximum slew mode.
- Optional **Keep CONTROL at target**, disabled by default.
- **Indenting**: target, 120-second hold, return to zero, final MEASURE.
- Editable JSON pressure routines with target, slew, dwell, and notes.
- Protected pressurization parameters behind a padlock and danger confirmation.
- Permanent sample-side and inlet-side leak indicators with editable thresholds.
- CSV telemetry and diagnostic logs at full received precision.
- Offline simulator for hardware-free validation.

## Software protections

- An increase of at least 10 bar requires confirmation.
- Slew above 0.5 bar/s or Maximum mode requires confirmation.
- Targets outside the detected module range are blocked.
- CONTROL is blocked when the positive-source margin is below 2 bar.
- If the source margin falls below 2 bar during CONTROL, automation stops and MEASURE is requested.
- After an interlock, CONTROL remains blocked until the source margin reaches 2.2 bar.
- Loss of required telemetry during CONTROL triggers a fail-safe MEASURE attempt.
- Automation timeouts request MEASURE.

These are software checks, not safety-rated interlocks.

## PACE configuration

### Ethernet

Configure the PACE with:

| Parameter | Value |
|---|---|
| Address mode | Static |
| IP address | `192.168.10.2` |
| Subnet mask | `255.255.255.0` |
| Gateway/DNS | Empty or `0.0.0.0` |
| Access control | Open |
| SCPI socket | TCP `5025` |

The program first tries the configured address. If requested, it then adds `192.168.10.1/24` only to a single safe dedicated Ethernet adapter that has no gateway and no unrelated IPv4 network. No adapter is changed if selection is ambiguous. The temporary address is removed at shutdown.

### RS-232

On the PACE select the RS-232 communication interface and SCPI protocol. The application defaults to:

| Parameter | Value |
|---|---|
| Baud rate | `9600` |
| Data bits | `8` |
| Parity | None |
| Stop bits | `1` |
| Flow control | None |
| Terminator | CR |

Baud rates from 2400 to 115200, None/Even/Odd parity, XON/XOFF, and RTS/CTS can be selected from the GUI. PACE and PC settings must match.

On Linux, add the user to the serial-port group if necessary:

```bash
sudo usermod -aG dialout "$USER"
```

Log out and back in after changing group membership.

## Running from source

Python 3.10 or newer is required:

```bash
cd cross_platform
python -m venv .venv
source .venv/bin/activate        # Linux
# .venv\Scripts\activate       # Windows
python -m pip install -r requirements.txt
python -m pace_controller
```

For a hardware-free test:

```bash
python -m pace_controller --simulate
```

## Reproducible local builds

The same scripts used by GitHub Actions are included:

```powershell
# Windows PowerShell
.\scripts\build_windows.ps1 -Version 1.0.0
```

```bash
# Linux
bash scripts/build_linux.sh 1.0.0
```

Artifacts are written to `cross_platform/dist`. GitHub Actions additionally regenerates the real GUI screenshot, builds this manual as PDF, verifies the frozen legacy source hash, generates SHA-256 checksums, creates an immutable semantic-version tag, and publishes every release asset.

## Data files

- Windows: `%LOCALAPPDATA%\PACE Controller`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/pace-controller`

The folder contains `PACE_controller_data.csv`, `PACE_controller_log.txt`, and `PACE_controller_settings.json`.

## Legacy Windows implementation

The validated PowerShell/WinForms **v0.3.1** remains unchanged in the repository root and in its immutable release tag. The cross-platform application is separate and does not alter that working implementation.

## Documentation and references

- [Complete manual](docs/PACE_Controller_Manual.md)
- [Druck PACE SCPI Remote Communications Manual K0472](https://druck.com/wp-content/uploads/2026/05/K0472-PACE-SCPI-Remote-Communications-Manual-EN.pdf)
- [Classic PACE 5000 support page](https://druck.com/product/pace-5000/)

PACE Controller is independent software and is not an official Druck product. Released under the [MIT License](../LICENSE).

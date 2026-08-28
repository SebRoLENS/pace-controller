#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-1.0.0}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-dev.txt

OUTPUT_NAME="PACE-Controller-v${VERSION}-Linux-x86_64"
python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --windowed \
  --name "$OUTPUT_NAME" \
  --icon assets/pace-controller.png \
  --paths src \
  --hidden-import serial \
  --hidden-import serial.tools.list_ports \
  pace_controller_launcher.py

test -x "dist/$OUTPUT_NAME"
tar -C dist -czf "dist/${OUTPUT_NAME}.tar.gz" "$OUTPUT_NAME"
echo "Built $PROJECT_ROOT/dist/${OUTPUT_NAME}.tar.gz"

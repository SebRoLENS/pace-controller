"""Application entry point."""

from __future__ import annotations

import argparse
import os
import sys


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="PACE Controller for Windows and Linux")
    result.add_argument("--language", choices=("en", "it"), default=None)
    result.add_argument("--simulate", action="store_true", help="Start with the offline simulator selected")
    result.add_argument("--screenshot", metavar="PNG", default="", help="Render the real GUI with simulated values and exit")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.screenshot and sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from .models import ConnectionKind
    from .service import PaceService
    from .storage import load_settings
    from .ui import MainWindow

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    application = QApplication(sys.argv[:1])
    application.setApplicationName("PACE Controller")
    application.setOrganizationName("SebRoLENS")

    settings = load_settings()
    if args.language:
        settings.language = args.language
    elif args.screenshot:
        settings.language = "en"
    if args.simulate or args.screenshot:
        settings.connection.kind = ConnectionKind.SIMULATOR

    service = PaceService(
        minimum_source_margin_bar=settings.minimum_source_margin_bar,
        source_margin_rearm_bar=settings.source_margin_rearm_bar,
    )
    window = MainWindow(service, settings, screenshot_path=args.screenshot)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())


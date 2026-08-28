"""PyInstaller-friendly launcher for the packaged application."""

from pace_controller.main import main


if __name__ == "__main__":
    raise SystemExit(main())

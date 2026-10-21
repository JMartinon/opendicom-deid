"""Absolute-import entry point for PyInstaller's script analysis."""

from opendicom_deid.__main__ import main
from opendicom_deid.core import anonymizer  # noqa: F401 -- validate frozen engine imports at startup

if __name__ == "__main__":
    raise SystemExit(main())

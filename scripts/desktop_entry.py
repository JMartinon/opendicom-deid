"""Absolute-import entry point for PyInstaller's script analysis."""

if __name__ == "__main__":
    import multiprocessing

    # PyInstaller workers reuse this executable on every OS. Divert them before
    # loading the application, parsing arguments or initializing any GUI code.
    multiprocessing.freeze_support()

    from opendicom_deid.__main__ import main
    from opendicom_deid.core import anonymizer  # noqa: F401 -- validate frozen engine imports at startup

    raise SystemExit(main())

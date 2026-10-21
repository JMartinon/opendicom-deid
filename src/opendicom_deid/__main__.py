"""Launch the desktop by default, or the CLI when arguments are supplied."""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import main as cli_main

        return cli_main()
    try:
        from .gui.app import main as gui_main
    except ImportError:
        print(
            "The graphical interface requires Python with Tcl/Tk. "
            "Use a packaged desktop build, or run 'opendicom-deid --help' for the CLI.",
            file=sys.stderr,
        )
        return 1
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())

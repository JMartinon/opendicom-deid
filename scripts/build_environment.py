"""Check the selected Python's GUI runtime before installing build dependencies.

Only the standard library is used. Tcl initialization does not require a display,
so this check also works on headless build hosts.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import asdict, dataclass


@dataclass
class BuildEnvironment:
    executable: str
    version: str
    base_executable: str
    is_venv: bool
    error: str | None = None
    tcl_version: str | None = None


def inspect_environment() -> BuildEnvironment:
    environment = BuildEnvironment(
        executable=sys.executable,
        version=platform.python_version(),
        base_executable=os.path.realpath(getattr(sys, "_base_executable", sys.executable)),
        is_venv=sys.prefix != sys.base_prefix,
    )
    if sys.version_info < (3, 12):
        environment.error = "Python 3.12 or newer is required."
        return environment
    try:
        import tkinter

        environment.tcl_version = tkinter.Tcl().eval("info patchlevel")
    except Exception as exc:
        # The import can fail without _tkinter/shared libraries; Tcl() can raise
        # TclError when its runtime scripts are absent. Neither requires a GUI.
        environment.error = f"Tcl/Tk is unavailable: {type(exc).__name__}: {exc}"
    return environment


def environment_help() -> str:
    if sys.platform.startswith("linux"):
        return (
            "For Ubuntu 24.04+ with its system Python, run:\n"
            "  sudo apt-get update\n"
            "  sudo apt-get install python3-tk python3-venv\n"
            "  PYTHON=/usr/bin/python3 bash scripts/build_linux.sh\n"
            "python3-tk belongs to the distribution's default Python; it does not add Tk "
            "to a custom Python 3.14 installation. For another Python, install matching "
            "Tk support from that Python's provider, or rebuild it with Tcl/Tk support.\n"
            "Tkinter cannot be installed with pip. Packaged desktop downloads already "
            "include Python and Tcl/Tk; see docs/distribution.md."
        )
    if sys.platform == "darwin":
        return (
            "Use a Python installation with Tcl/Tk (such as the python.org installer), "
            "or install the matching python-tk package for your Homebrew Python version.\n"
            "Recreate the build virtual environment with that interpreter. "
            "Tkinter cannot be installed with pip. See docs/distribution.md."
        )
    return (
        "Install or repair Python with its Tcl/Tk component enabled, then recreate the "
        "build virtual environment using that Python. Tkinter cannot be installed "
        "with pip. Packaged desktop downloads include the runtime; see docs/distribution.md."
    )


def require_build_environment() -> BuildEnvironment:
    environment = inspect_environment()
    if environment.error:
        raise SystemExit(
            f"Cannot build the GUI with {environment.executable} "
            f"(Python {environment.version}).\n{environment.error}\n\n{environment_help()}"
        )
    return environment


def main() -> int:
    if sys.argv[1:] == ["--json"]:
        environment = inspect_environment()
        print(json.dumps(asdict(environment)))
        return int(environment.error is not None)
    environment = require_build_environment()
    print(
        f"Build interpreter: {environment.executable} "
        f"(Python {environment.version}, Tcl {environment.tcl_version})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

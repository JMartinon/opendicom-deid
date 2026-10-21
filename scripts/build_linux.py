"""Select a Tk-capable Python and build without modifying the system Python."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from build_environment import BuildEnvironment, environment_help

ROOT = Path(__file__).resolve().parents[1]


def probe(executable: str) -> BuildEnvironment:
    try:
        result = subprocess.run(
            [executable, str(ROOT / "scripts" / "build_environment.py"), "--json"],
            text=True,
            capture_output=True,
            check=False,
        )
        environment = BuildEnvironment(**json.loads(result.stdout))
        if result.returncode and not environment.error:
            environment.error = f"Runtime check exited with status {result.returncode}."
        return environment
    except (OSError, ValueError, TypeError) as exc:
        return BuildEnvironment(executable, "unknown", executable, False, str(exc))


def select_python(requested: str | None) -> BuildEnvironment:
    # An explicit choice must never silently build with a different interpreter.
    candidates = [requested] if requested else [shutil.which("python3"), "/usr/bin/python3"]
    failures = []
    seen = set()
    for executable in candidates:
        if not executable or executable in seen:
            continue
        seen.add(executable)
        environment = probe(executable)
        if not environment.error:
            if failures:
                print("\n".join(failures), file=sys.stderr, flush=True)
                print("Using the system Python fallback for this build.", flush=True)
            return environment
        failures.append(
            f"{environment.executable} (Python {environment.version}): {environment.error}"
        )
    raise SystemExit(
        "No usable GUI build interpreter was found.\n"
        + "\n".join(failures)
        + ("\nPYTHON was explicitly set; automatic fallback is disabled." if requested else "")
        + "\n\n"
        + environment_help()
    )


def prepare_python(selected: BuildEnvironment) -> str:
    if selected.is_venv:
        return selected.executable
    directory = ROOT / ".venv-build-linux"
    executable = directory / "bin" / "python"
    if directory.exists() or directory.is_symlink():
        environment = probe(str(executable))
        if (
            environment.error
            or not environment.is_venv
            or environment.base_executable != selected.base_executable
            or environment.version != selected.version
        ):
            raise SystemExit(
                f"Existing build environment {directory} is incompatible with "
                f"{selected.executable} (Python {selected.version}).\n"
                "It has been left unchanged. Rename it and rerun the build, or set "
                "PYTHON to a compatible virtual environment's Python."
                + (f"\n{environment.error}" if environment.error else "")
            )
    else:
        print(f"Creating isolated build environment: {directory}", flush=True)
        try:
            subprocess.run([selected.executable, "-m", "venv", str(directory)], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SystemExit(
                "Could not create the build virtual environment. Install venv/ensurepip "
                "for the selected Python (python3-venv for Ubuntu's system Python).\n"
                f"If {directory} was partially created, rename it before retrying.\n"
                f"{exc}"
            ) from exc
        environment = probe(str(executable))
        if environment.error:
            raise SystemExit(f"Build virtual environment is unusable: {environment.error}")
    return str(executable)


def main() -> int:
    if not sys.platform.startswith("linux"):
        raise SystemExit("Run build_linux.sh on Linux; use the native build script for this OS.")
    selected = select_python(os.environ.get("PYTHON"))
    executable = prepare_python(selected)
    print(
        f"Build interpreter: {executable} "
        f"(Python {selected.version}, Tcl {selected.tcl_version})",
        flush=True,
    )
    try:
        subprocess.run([executable, "-m", "pip", "install", ".[build,dev]"], cwd=ROOT, check=True)
        subprocess.run([executable, "-m", "pytest"], cwd=ROOT, check=True)
        subprocess.run([executable, "scripts/build.py"], cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

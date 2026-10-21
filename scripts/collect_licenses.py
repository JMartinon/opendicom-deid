"""Collect license texts from the exact installed distributions used in a build.

This script runs at build time only. It performs no network requests. Distribution
metadata is followed without optional development/build extras. The PyInstaller
license and bootloader exception are included separately.
"""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import sys
import sysconfig
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def collect(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "LICENSE", destination / "OpenDICOM-De-ID.txt")
    shutil.copytree(ROOT / "THIRD_PARTY_LICENSES", destination / "notices", dirs_exist_ok=True)
    pending = ["opendicom-deid", "pyinstaller"]
    visited: set[str] = set()
    inventory: list[dict[str, str]] = []
    while pending:
        name = canonicalize_name(pending.pop())
        if name in visited:
            continue
        visited.add(name)
        dist = importlib.metadata.distribution(name)
        # PyInstaller itself is build-only: its exception is relevant to the
        # bootloader; its own build dependencies are not application dependencies.
        if name != "pyinstaller":
            for specification in dist.requires or []:
                requirement = Requirement(specification)
                if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                    pending.append(requirement.name)
        files = [
            item
            for item in dist.files or []
            if any(part.endswith(".dist-info") for part in item.parts)
            and (
                "licenses" in item.parts
                or item.name.lower().startswith(("license", "licence", "copying", "notice"))
            )
            and dist.locate_file(item).is_file()
        ]
        if not files and name != "opendicom-deid":
            raise RuntimeError(f"No license text found for installed distribution {name}.")
        for item in files:
            # No absolute paths or metadata from the build machine are exposed.
            output = destination / "packages" / name / Path(*item.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dist.locate_file(item), output)
        inventory.append({"name": name, "version": dist.version})

    candidates = [
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
        Path(sys.base_prefix) / "license.txt",
    ]
    python_license = next((path for path in candidates if path.is_file()), None)
    if python_license is None:
        raise RuntimeError(
            "Python's license file was not found; use an official Python distribution."
        )
    shutil.copy2(python_license, destination / "Python.txt")
    inventory.append({"name": "Python", "version": sys.version.split()[0]})
    (destination / "versions.json").write_text(
        json.dumps(sorted(inventory, key=lambda entry: entry["name"]), indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    collect(ROOT / "build" / "licenses")

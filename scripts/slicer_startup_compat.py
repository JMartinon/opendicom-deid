"""Apply both OpenDICOM viewer workarounds inside a running 3D Slicer.

Use Slicer's --python-script option, or call apply_compatibility() from its
Python console. DICOM files are never changed. The database helper saves a
reversible setting; the reader guard lasts only for this Slicer process.
"""

from __future__ import annotations

import runpy
from pathlib import Path


def apply_compatibility() -> bool:
    directory = Path(__file__).resolve().parent
    database = runpy.run_path(str(directory / "slicer_database_compat.py"))
    reader = runpy.run_path(str(directory / "slicer_reader_compat.py"))
    database_ok = database["apply_compatibility"]()
    reader_ok = reader["apply_compatibility"]()
    if database_ok and reader_ok:
        print(
            "OpenDICOM Slicer compatibility is ready. Re-examine the dataset in DICOM. "
            "Private ViewRay beams remain unsupported; empty ROIs remain empty."
        )
    return database_ok and reader_ok


if __name__ == "__main__":
    apply_compatibility()

"""Inspect storage formats locally without reading pixels or printing patient identifiers.

Run from the repository after pulling updates; rebuilding the application is unnecessary:
    python scripts/inspect_storage.py /path/to/export --file-id 199

To reveal a numeric private SOP class UID for local investigation only:
    python scripts/inspect_storage.py /path/to/export --file-id 199 --show-private-class-uids

File numbers match a recursive app scan of the same unchanged input directory,
including intervening non-DICOM files. No filenames, patient identifiers, or
instance/study/series UIDs are printed. This command never writes or uploads data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Prefer the checked-out source, even when an older application is installed in
# this Python environment. Only the existing runtime dependencies are needed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opendicom_deid.core.storage_inspection import inspect_storage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", type=Path, help="The same input directory selected in the app.")
    parser.add_argument("--file-id", type=int, help="Inspect only this positive File number, e.g. 199.")
    parser.add_argument("--no-recursive", action="store_true", help="Match an app scan with recursion disabled.")
    parser.add_argument(
        "--show-private-class-uids", action="store_true",
        help="Reveal valid numeric unknown/private SOP class UIDs for local inspection only.",
    )
    args = parser.parse_args(argv)
    result = inspect_storage(
        args.directory,
        file_id=args.file_id,
        recursive=not args.no_recursive,
        show_private_class_uids=args.show_private_class_uids,
    )
    print(json.dumps(result, indent=2))
    if result.get("status") in {"FAILED", "CANCELLED"}:
        return 2
    results = result["files"]
    return 0 if results and all(item["status"] == "INSPECTED" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

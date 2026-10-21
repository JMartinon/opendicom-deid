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
import re
import sys
from pathlib import Path

# Prefer the checked-out source, even when an older application is installed in
# this Python environment. Only the existing runtime dependencies are needed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pydicom  # noqa: E402
from pydicom.errors import InvalidDicomError  # noqa: E402
from pydicom.uid import UID_dictionary  # noqa: E402

from opendicom_deid.core.diagnostics import private_diagnostics  # noqa: E402
from opendicom_deid.core.models import Finding  # noqa: E402
from opendicom_deid.core.scanner import _paths  # noqa: E402
from opendicom_deid.core.sop_classes import (  # noqa: E402
    SUPPORTED_SOP_CLASSES,
    modality_label,
)


def _uid_description(value, kind: str, show_private_class_uids: bool) -> dict:
    text = value if isinstance(value, str) else ""
    definition = UID_dictionary.get(text)
    if definition and definition[1] == kind:
        return {"name": definition[0], "uid": text}
    if not text:
        return {"name": "Missing or invalid", "uid": None}
    numeric_uid = len(text) <= 64 and re.fullmatch(
        r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*", text
    ) is not None
    if kind == "SOP Class" and show_private_class_uids and numeric_uid:
        return {"name": "Unknown or private SOP class", "uid": text}
    return {"name": "Unrecognized or private", "uid": "REDACTED"}


def inspect_header(path: Path, file_id: int, *, show_private_class_uids: bool = False) -> dict:
    """Read only selected header values and return a deliberately small safe record."""
    label = f"File {file_id:06d}"
    failure = {
        "file_id": label,
        "status": "HEADER_READ_FAILED",
        "message": "The selected file header could not be safely inspected.",
    }
    try:
        with private_diagnostics() as diagnostics:
            try:
                ds = pydicom.dcmread(
                    path, stop_before_pixels=True, specific_tags=["SOPClassUID", "Modality"]
                )
            except InvalidDicomError:
                ds = pydicom.dcmread(
                    path, force=True, stop_before_pixels=True,
                    specific_tags=["SOPClassUID", "Modality"],
                )
            sop_class = ds.get("SOPClassUID")
            if not sop_class:
                return failure
            meta = getattr(ds, "file_meta", {})
            result = {
                "file_id": label,
                "status": "INSPECTED",
                "sop_class": _uid_description(sop_class, "SOP Class", show_private_class_uids),
                "supported_storage_class": str(sop_class) in SUPPORTED_SOP_CLASSES,
                "modality": modality_label(ds),
                "file_meta": {
                    "media_storage_sop_class": _uid_description(
                        meta.get("MediaStorageSOPClassUID"), "SOP Class", show_private_class_uids
                    ),
                    "transfer_syntax": _uid_description(
                        meta.get("TransferSyntaxUID"), "Transfer Syntax", False
                    ),
                },
            }
        return failure if diagnostics.has_warnings else result
    except Exception:
        return failure


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
    if args.file_id is not None and args.file_id < 1:
        print(json.dumps({"status": "FAILED", "message": "File numbers must be positive."}))
        return 2
    try:
        root = args.directory.expanduser().resolve()
        if not root.is_dir():
            raise ValueError
        findings: list[Finding] = []
        paths = _paths(root, not args.no_recursive, findings, None)
    except Exception:
        print(json.dumps({"status": "FAILED", "message": "Select a readable input directory."}))
        return 2
    if findings:
        # Do not select a possibly shifted label when parts of the inventory
        # cannot be traversed or include links or special files.
        print(json.dumps({"status": "FAILED", "message": "The input inventory contains excluded or unreadable entries."}))
        return 2
    if args.file_id is not None and args.file_id > len(paths):
        print(json.dumps({"status": "FAILED", "message": "The requested File number is outside this inventory."}))
        return 2
    selected = (
        [(args.file_id, paths[args.file_id - 1])]
        if args.file_id is not None else enumerate(paths, 1)
    )
    results = [
        inspect_header(path, number, show_private_class_uids=args.show_private_class_uids)
        for number, path in selected
    ]
    print(json.dumps({"files": results}, indent=2))
    return 0 if results and all(item["status"] == "INSPECTED" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

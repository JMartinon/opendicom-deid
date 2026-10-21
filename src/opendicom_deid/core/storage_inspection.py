"""Inspect technical storage headers locally without exposing patient identifiers."""

from __future__ import annotations

import re
from pathlib import Path
from threading import Event

import pydicom
from pydicom.errors import InvalidDicomError
from pydicom.uid import UID_dictionary

from .diagnostics import private_diagnostics
from .models import Cancelled, Finding, check_cancel
from .scanner import _paths
from .sop_classes import (
    KNOWN_PRIVATE_STORAGE_CLASSES,
    SUPPORTED_SOP_CLASSES,
    VIEWRAY_RT_PLAN_STORAGE,
    modality_label,
)


def _uid_description(value, kind: str, show_private_class_uids: bool) -> dict:
    text = value if isinstance(value, str) else ""
    definition = UID_dictionary.get(text)
    if kind == "SOP Class" and text in KNOWN_PRIVATE_STORAGE_CLASSES:
        return {"name": KNOWN_PRIVATE_STORAGE_CLASSES[text], "uid": text}
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
            if str(sop_class) == VIEWRAY_RT_PLAN_STORAGE:
                result["support_note"] = (
                    "Public-data research export requires explicit opt-in. "
                    "Full proprietary plan preservation is unsupported."
                )
        return failure if diagnostics.has_warnings else result
    except Exception:
        return failure


def inspect_storage(
    directory: Path,
    file_id: int | None = None,
    recursive: bool = True,
    show_private_class_uids: bool = False,
    cancel: Event | None = None,
) -> dict:
    """Return technical headers in scan order, or a safe failure/cancellation payload.

    File numbers match an app scan of the same unchanged directory and recursion
    setting, including non-DICOM entries. No files are written and no data is
    uploaded. Cancellation discards partial results.
    """
    try:
        check_cancel(cancel)
        if file_id is not None and (type(file_id) is not int or file_id < 1):
            return {"status": "FAILED", "message": "File numbers must be positive."}
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            return {"status": "FAILED", "message": "Select a readable input directory."}
        findings: list[Finding] = []
        paths = _paths(root, recursive, findings, cancel)
        check_cancel(cancel)
        if findings:
            # A partially excluded inventory could shift labels relative to the
            # original scan, so never guess which file the caller meant.
            return {
                "status": "FAILED",
                "message": "The input inventory contains excluded or unreadable entries.",
            }
        if file_id is not None and file_id > len(paths):
            return {
                "status": "FAILED",
                "message": "The requested File number is outside this inventory.",
            }
        selected = (
            [(file_id, paths[file_id - 1])]
            if file_id is not None else enumerate(paths, 1)
        )
        results = []
        for number, path in selected:
            check_cancel(cancel)
            results.append(inspect_header(
                path, number, show_private_class_uids=show_private_class_uids,
            ))
        check_cancel(cancel)
        return {"files": results}
    except Cancelled:
        return {"status": "CANCELLED", "message": "Storage inspection was cancelled."}
    except Exception:
        return {"status": "FAILED", "message": "Select a readable input directory."}

"""First pass: inventory, identity grouping, duplicates and reference analysis."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from threading import Event

from pydicom.dataset import Dataset
from pydicom.uid import UID

from .inspection import capture_snapshot
from .models import FileRecord, Finding, Progress, ProgressCallback, ScanResult, check_cancel
from .reader import ReadError, file_digest, looks_like_dicom, read_dicom
from .sop_classes import (
    KNOWN_MODALITIES,
    SUPPORTED_SOP_CLASSES,
    VIEWRAY_PLAN_REVIEW_REQUIRED,
    VIEWRAY_PLAN_WARNING,
    VIEWRAY_RT_PLAN_STORAGE,
    modality_error,
    modality_label,
    unsupported_object_message,
)
from .structure import structural_error


def patient_key(ds: Dataset) -> tuple[str, ...]:
    """Use IDs with issuer scope; never merge by a patient's name alone."""
    identifier = str(ds.get("PatientID", "")).strip()
    if identifier:
        qualifiers = ds.get("IssuerOfPatientIDQualifiersSequence", [])
        scopes = tuple(
            str(item.get(keyword, ""))
            for item in qualifiers
            for keyword in ("LocalNamespaceEntityID", "UniversalEntityID", "UniversalEntityIDType")
        )
        return ("id", str(ds.get("IssuerOfPatientID", "")), *scopes, identifier)
    return ("study", str(ds.get("StudyInstanceUID", "")))


def _paths(root: Path, recursive: bool, findings: list[Finding], cancel: Event | None) -> list[Path]:
    paths: list[Path] = []

    def unreadable(_: OSError) -> None:
        findings.append(Finding("FAIL", "DIRECTORY_UNREADABLE", "An input directory is unreadable."))

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        check_cancel(cancel)
        dirs.sort()
        for name in list(dirs):
            if (Path(directory) / name).is_symlink():
                dirs.remove(name)
                findings.append(Finding("FAIL", "SYMLINK", "A symbolic-link directory was excluded."))
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink():
                findings.append(Finding("FAIL", "SYMLINK", "A symbolic-link file was excluded."))
            elif path.is_file():
                paths.append(path)
            else:
                findings.append(Finding("FAIL", "SPECIAL_FILE", "An input entry is not a regular file."))
        if not recursive:
            break
    return paths


def scan_directory(
    path: str | Path,
    recursive: bool = True,
    cancel: Event | None = None,
    progress: ProgressCallback | None = None,
    *,
    allow_viewray_public_plan: bool = False,
) -> ScanResult:
    """Inventory without retaining pixel buffers or writing identifiable intermediate files."""
    root = Path(path).expanduser().resolve()
    result = ScanResult(root)
    if not root.is_dir():
        result.findings.append(Finding("FAIL", "INPUT_DIRECTORY", "Select a readable input directory."))
        return result
    paths = _paths(root, recursive, result.findings, cancel)
    result.source_files = tuple(paths)
    duplicates: dict[str, FileRecord] = {}
    study_patients: dict[str, tuple[str, ...]] = {}
    series_studies: dict[str, str] = {}
    identity_details: dict[tuple[str, ...], dict[str, str]] = {}
    for index, source in enumerate(paths, 1):
        check_cancel(cancel)
        label = f"File {index:06d}"
        try:
            before_hash = file_digest(source)
            ds = read_dicom(source)
            if file_digest(source) != before_hash:
                raise ReadError("Source changed during scan.")
        except (ReadError, OSError):
            if looks_like_dicom(source):
                result.findings.append(Finding(
                    "FAIL", "DICOM_READ", "A DICOM candidate could not be safely read.", label
                ))
            else:
                result.skipped += 1
            if progress:
                progress(Progress("Scanning", index, len(paths)))
            continue
        sop_class = str(ds.SOPClassUID)
        modality = modality_label(ds)
        if sop_class not in SUPPORTED_SOP_CLASSES:
            result.findings.append(Finding(
                "FAIL", "UNSUPPORTED_OBJECT", unsupported_object_message(ds), label
            ))
            continue
        if problem := modality_error(ds):
            result.findings.append(Finding(
                "FAIL", "MODALITY", problem, label
            ))
            continue
        if sop_class == VIEWRAY_RT_PLAN_STORAGE and not allow_viewray_public_plan:
            result.findings.append(Finding(
                "FAIL", "VIEWRAY_PLAN_REVIEW_REQUIRED", VIEWRAY_PLAN_REVIEW_REQUIRED, label
            ))
            continue
        if ds.get("Modality") and str(ds.Modality).strip() not in KNOWN_MODALITIES:
            result.findings.append(Finding(
                "WARNING", "UNRECOGNIZED_MODALITY",
                "A source modality uses an extension code. It is preserved unchanged; "
                "review vendor-specific metadata before sharing.", label
            ))
        structure_problem = structural_error(ds)
        if structure_problem:
            result.findings.append(Finding("FAIL", "INVALID_STRUCTURE", structure_problem, label))
            continue
        try:
            for keyword in ("SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"):
                value = str(ds.get(keyword, ""))
                if not value or not UID(value).is_valid:
                    raise ValueError()
            meta = getattr(ds, "file_meta", Dataset())
            for keyword, expected in (
                ("MediaStorageSOPInstanceUID", str(ds.SOPInstanceUID)),
                ("MediaStorageSOPClassUID", sop_class),
            ):
                if keyword in meta and str(meta.get(keyword)) != expected:
                    raise ValueError()
            if "TransferSyntaxUID" in meta and not UID(meta.TransferSyntaxUID).is_transfer_syntax:
                raise ValueError()
            snapshot = capture_snapshot(ds)
            from .inspection import walk_elements
            for _, element in walk_elements(ds):
                if element.keyword in {
                    "ReferencedSOPInstanceUID", "MultiFrameSourceSOPInstanceUID",
                    "SOPInstanceUIDOfConcatenationSource",
                } and not element.value:
                    raise ValueError()
        except Exception:
            result.findings.append(Finding(
                "FAIL", "INPUT_METADATA", "Required identifiers or input file metadata are invalid.", label
            ))
            continue
        identity = patient_key(ds)
        record = FileRecord(source, label, source.parent.relative_to(root), before_hash, snapshot, identity, modality)
        result.files.append(record)
        if sop_class == VIEWRAY_RT_PLAN_STORAGE:
            result.findings.append(Finding(
                "WARNING", "VIEWRAY_PRIVATE_PLAN", VIEWRAY_PLAN_WARNING, label
            ))
        if sop_class == "1.2.840.10008.5.1.4.1.1.481.2" and "PixelData" not in ds:
            result.findings.append(Finding(
                "WARNING", "NON_GRID_DOSE",
                "A dose object has no image grid. Its non-grid dose content is preserved; review it in a compatible viewer.", label
            ))
        if ds.get("BurnedInAnnotation") == "YES":
            result.findings.append(Finding(
                "WARNING", "BURNED_IN_ANNOTATION",
                "An object declares burned-in annotations. Pixel content is unchanged and needs review.", label
            ))
        if ds.get("RecognizableVisualFeatures") == "YES":
            result.findings.append(Finding(
                "WARNING", "RECOGNIZABLE_FEATURES",
                "An object declares recognizable visual features. Pixel content needs privacy review.", label
            ))
        if identity[0] == "study":
            result.findings.append(Finding(
                "WARNING", "MISSING_PATIENT_ID", "Missing patient ID: pseudonym linkage is limited to this study.", label
            ))
        details = identity_details.setdefault(identity, {})
        for keyword in ("PatientName", "PatientBirthDate"):
            value = str(ds.get(keyword, "")).strip()
            if value and details.get(keyword, value) != value:
                result.findings.append(Finding(
                    "FAIL", "PATIENT_CONFLICT", "One patient identity has conflicting demographic information.", label
                ))
            if value:
                details[keyword] = value
        study = str(ds.StudyInstanceUID)
        series = str(ds.SeriesInstanceUID)
        if study_patients.setdefault(study, identity) != identity:
            result.findings.append(Finding(
                "FAIL", "STUDY_PATIENT_CONFLICT", "One study is assigned to multiple patient identities.", label
            ))
        if series_studies.setdefault(series, study) != study:
            result.findings.append(Finding(
                "FAIL", "SERIES_STUDY_CONFLICT", "One series is assigned to multiple studies.", label
            ))
        duplicate = duplicates.setdefault(snapshot.sop_uid, record)
        if duplicate is not record:
            # Byte-identical copies are unequivocal. Other representations fail conservatively:
            # vendor/private contents removed by the reader must not hide conflicting copies.
            if duplicate.sha256 == record.sha256:
                result.findings.append(Finding(
                    "WARNING", "DUPLICATE_COPY", "An identical instance copy will retain the same replacement UID.", label
                ))
            else:
                result.findings.append(Finding(
                    "FAIL", "CONFLICTING_DUPLICATE", "Non-identical files share an instance UID; resolve the conflict before processing.", label
                ))
        if progress:
            progress(Progress("Scanning", index, len(paths)))
    result.modalities = dict(Counter(record.modality for record in result.files))
    if result.skipped:
        result.findings.append(Finding(
            "WARNING", "NON_DICOM_SKIPPED", f"{result.skipped} unrecognized non-DICOM files were excluded."
        ))
    if not result.files:
        result.findings.append(Finding("FAIL", "NO_DICOM", "No supported DICOM objects were found."))
    return result

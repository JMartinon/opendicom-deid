"""First pass: inventory, identity grouping, duplicates and reference analysis."""

from __future__ import annotations

import os
import stat
from collections import Counter
from pathlib import Path
from threading import Event

from pydicom.dataset import Dataset
from pydicom.uid import UID
from pydicom.valuerep import PersonName

from .filesystem import filesystem_path, safe_error_details
from .inspection import capture_snapshot
from .models import FileRecord, Finding, Progress, ProgressCallback, ScanResult, check_cancel
from .reader import ReadError, file_digest, looks_like_dicom, read_dicom
from .reference_classes import reference_class_findings, reference_class_repairs
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

type _DemographicValue = str | tuple[str, ...]


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


def _patient_name_for_comparison(
    name: PersonName, *, ignore_case: bool = False,
) -> _DemographicValue:
    """Compare PN components within an identity; never rewrite the source name."""
    text = str(name)
    groups = text.split("=")
    if len(groups) > 3 or any(group.count("^") > 4 for group in groups):
        return text  # Do not equate malformed structures with valid names.
    normalized = "=".join(
        "^".join(part.strip(" ") for part in group.split("^")).rstrip("^")
        for group in groups
    ).rstrip("=")
    if ignore_case:
        # Fold character by character so É/é match but ß and SS stay distinct.
        # Component positions, spelling, punctuation and accents remain significant.
        return tuple(char.casefold() for char in normalized)
    return normalized


def _paths(root: Path, recursive: bool, findings: list[Finding], cancel: Event | None) -> list[Path]:
    paths: list[Path] = []
    entry_number = 0

    def unreadable(error: OSError) -> None:
        check_cancel(cancel)
        findings.append(Finding(
            "FAIL", "DIRECTORY_UNREADABLE",
            f"An input directory could not be listed ({safe_error_details(error)}). "
            "Check folder access and that the export is complete and still available.",
        ))

    def inspect_entry(path: Path, is_directory: bool) -> bool:
        nonlocal entry_number
        check_cancel(cancel)
        entry_number += 1
        # Entry labels describe inventory failures, never a guessed DICOM File number.
        label = f"Entry {entry_number:06d}"
        try:
            info = path.lstat()
            # Windows name-surrogate reparse points include junctions as well
            # as symbolic links. Do not traverse them or read their targets.
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) & 0x20000000:
                findings.append(Finding(
                    "FAIL", "SYMLINK", "A symbolic link or junction was excluded from the input.", label,
                ))
                return False
            # Use stat rather than is_file/is_dir, which can suppress useful
            # access errors and mislabel inaccessible paths as special files.
            mode = path.stat().st_mode
        except OSError as error:
            findings.append(Finding(
                "FAIL", "ENTRY_ACCESS",
                f"An input entry could not be accessed ({safe_error_details(error)}). "
                "Check that the export is complete, the files are available locally, "
                "and you have read access.", label,
            ))
            return False
        if is_directory and stat.S_ISDIR(mode):
            return True
        if not is_directory and stat.S_ISREG(mode):
            paths.append(path)
            return True
        if stat.S_ISDIR(mode) or stat.S_ISREG(mode):
            findings.append(Finding(
                "FAIL", "ENTRY_CHANGED",
                "An input entry changed type during inventory. Wait for the export to finish, then rescan.",
                label,
            ))
        else:
            findings.append(Finding(
                "FAIL", "SPECIAL_FILE", "An input entry is a special filesystem object, not a regular file or directory.",
                label,
            ))
        return False

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        check_cancel(cancel)
        dirs.sort()
        for name in list(dirs):
            if not inspect_entry(Path(directory) / name, True):
                dirs.remove(name)
        for name in sorted(files):
            inspect_entry(Path(directory) / name, False)
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
    result = ScanResult(Path(path))
    try:
        root = filesystem_path(path)
        result.input_dir = root
        directory = stat.S_ISDIR(root.stat().st_mode)
    except (OSError, ValueError) as error:
        result.findings.append(Finding(
            "FAIL", "INPUT_DIRECTORY",
            f"The input directory could not be accessed ({safe_error_details(error)}). "
            "Check its location, availability and read permissions.",
        ))
        return result
    if not directory:
        result.findings.append(Finding("FAIL", "INPUT_DIRECTORY", "Select a readable input directory."))
        return result
    if progress:
        progress(Progress("Inventory", 0, 0))
    paths = _paths(root, recursive, result.findings, cancel)
    result.source_files = tuple(paths)
    duplicates: dict[str, FileRecord] = {}
    study_patients: dict[str, tuple[str, ...]] = {}
    series_studies: dict[str, str] = {}
    identity_details: dict[tuple[str, ...], dict[str, tuple[_DemographicValue, str]]] = {}
    for index, source in enumerate(paths, 1):
        check_cancel(cancel)
        if progress:
            progress(Progress("Scanning", index - 1, len(paths)))
        label = f"File {index:06d}"
        try:
            before_hash = file_digest(source)
            ds = read_dicom(source)
            if file_digest(source) != before_hash:
                raise ReadError("Source changed during scan.")
        except OSError as error:
            result.findings.append(Finding(
                "FAIL", "FILE_ACCESS",
                f"An input file could not be read ({safe_error_details(error)}). "
                "Check that the export is complete, the file is available locally, "
                "and you have read access.", label,
            ))
            continue
        except ReadError:
            if looks_like_dicom(source):
                result.findings.append(Finding(
                    "FAIL", "DICOM_READ", "A DICOM candidate could not be safely read.", label
                ))
            else:
                result.skipped += 1
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
        location = f"{label} ({modality})"
        for keyword, field in (
            ("PatientName", "Patient Name (0010,0010)"),
            ("PatientBirthDate", "Patient Birth Date (0010,0030)"),
        ):
            raw = ds.get(keyword)
            value = (
                _patient_name_for_comparison(raw, ignore_case=identity[0] == "id")
                if keyword == "PatientName" and isinstance(raw, PersonName)
                else str(raw or "").strip()
            )
            if not value:
                continue
            previous = details.get(keyword)
            if previous is not None and previous[0] != value:
                result.findings.append(Finding(
                    "FAIL", "PATIENT_CONFLICT",
                    f"{field} differs between {previous[1]} and {location} for the same "
                    "patient identity. Verify both files in the source export.", label,
                ))
            details[keyword] = (value, location)
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
        progress(Progress("Scanning", len(paths), len(paths)))
    result.modalities = dict(Counter(record.modality for record in result.files))
    repairs = reference_class_repairs(result.files, enabled=allow_viewray_public_plan)
    result.findings.extend(reference_class_findings(
        [(record.file_id, record.snapshot) for record in result.files], repairs,
    ))
    if result.skipped:
        result.findings.append(Finding(
            "WARNING", "NON_DICOM_SKIPPED", f"{result.skipped} unrecognized non-DICOM files were excluded."
        ))
    if not result.files:
        result.findings.append(Finding("FAIL", "NO_DICOM", "No supported DICOM objects were found."))
    return result

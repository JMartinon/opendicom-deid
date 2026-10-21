"""Independent validation of saved output, with source-to-output graph comparison.

The validator reads files again; successful writes do not imply validation passes.
All public findings use fixed messages and neutral file labels to avoid leaking PHI.
Automated checks never establish that pixels or unusual metadata are anonymous.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Literal

from pydicom.dataset import Dataset
from pydicom.uid import UID

from .inspection import Snapshot, capture_snapshot, walk_elements
from .models import (
    ExpectedOutput,
    Finding,
    Options,
    Progress,
    ProgressCallback,
    ScanResult,
    check_cancel,
)
from .policy import metadata_issues
from .reader import looks_like_dicom, read_dicom
from .report import Report
from .structure import structural_error


def _finding(
    severity: Literal["PASS", "WARNING", "FAIL"], code: str,
    message: str, file_id: str | None = None,
) -> Finding:
    return Finding(severity, code, message, file_id)


def _metadata_findings(
    ds: Dataset, snapshot: Snapshot, file_id: str, *, pseudonym: str | None = None,
    options: Options | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    if structural_error(ds):
        findings.append(_finding(
            "FAIL", "INVALID_STRUCTURE", "Image payload or selected encoding metadata is invalid.", file_id
        ))
    date_mode = options.date_mode if options else (
        "shift" if ds.get("LongitudinalTemporalInformationModified") == "MODIFIED" else "remove"
    )
    policy_issues = metadata_issues(ds, pseudonym=pseudonym, date_mode=date_mode)
    meta = getattr(ds, "file_meta", None)
    if (
        not meta
        or str(meta.get("MediaStorageSOPInstanceUID", "")) != snapshot.sop_uid
        or str(meta.get("MediaStorageSOPClassUID", "")) != snapshot.sop_class_uid
        or not meta.get("TransferSyntaxUID")
        or not UID(str(meta.get("TransferSyntaxUID", ""))).is_transfer_syntax
    ):
        findings.append(_finding(
            "FAIL", "FILE_META_MISMATCH", "DICOM file metadata is missing or inconsistent.", file_id
        ))
    allowed_file_meta = {
        0x00020000, 0x00020001, 0x00020002, 0x00020003, 0x00020010, 0x00020012, 0x00020013,
    }
    if getattr(ds, "preamble", None) != b"\0" * 128 or (
        meta and any(int(tag) not in allowed_file_meta for tag in meta.keys())
    ):
        findings.append(_finding(
            "FAIL", "RESIDUAL_FILE_METADATA", "Unexpected preamble or file metadata remains.", file_id
        ))
    from .scanner import SUPPORTED_SOP_CLASSES
    if snapshot.sop_class_uid not in SUPPORTED_SOP_CLASSES or str(ds.get("Modality", "")) not in (
        SUPPORTED_SOP_CLASSES.get(snapshot.sop_class_uid, frozenset())
    ):
        findings.append(_finding(
            "FAIL", "UNSUPPORTED_OBJECT", "An unsupported or inconsistent DICOM object was found.", file_id
        ))
    if snapshot.private_count:
        findings.append(_finding(
            "FAIL", "PRIVATE_TAGS", "Unexpected private metadata remains in the output.", file_id
        ))
    if str(ds.get("PatientIdentityRemoved", "")) != "YES" or not ds.get("DeidentificationMethod"):
        findings.append(_finding(
            "FAIL", "DEIDENTIFICATION_MARKERS", "De-identification metadata markers are missing.",
            file_id,
        ))
    if pseudonym is not None and (
        snapshot.patient_id != pseudonym or snapshot.patient_name != pseudonym
    ):
        findings.append(_finding(
            "FAIL", "PATIENT_PSEUDONYM", "A patient pseudonym is missing or inconsistent.", file_id
        ))
    elif not snapshot.patient_id or not snapshot.patient_name:
        findings.append(_finding(
            "FAIL", "PATIENT_PSEUDONYM", "A patient pseudonym is missing or inconsistent.", file_id
        ))
    suspect_metadata = bool(policy_issues)
    invalid_uid = False
    for _, elem in walk_elements(ds):
        if elem.VR == "UI":
            from .inspection import uid_values
            from .uid_mapper import uid_kind
            invalid_uid |= any(not UID(value).is_valid for value in uid_values(elem))
            suspect_metadata |= uid_kind(elem) in {"remove", "unknown"} and bool(elem.value)
        if elem.VR == "PN" and elem.value:
            allowed = pseudonym or snapshot.patient_name
            suspect_metadata |= str(elem.value) not in {allowed, "ANONYMIZED"}
        if elem.keyword in {
            "AccessionNumber", "OtherPatientIDs", "OtherPatientNames", "PatientAddress",
            "PatientTelephoneNumbers", "InstitutionName", "InstitutionAddress",
            "ReferringPhysicianName", "DeviceSerialNumber", "StationName", "MedicalRecordLocator",
        } and str(elem.value).strip() not in {"", "ANONYMIZED"}:
            suspect_metadata = True
        if elem.keyword in {"PatientBirthDate", "PatientBirthTime"} and elem.value:
            suspect_metadata = True
        if options is not None and options.date_mode == "remove":
            if elem.VR in {"DA", "DT", "TM"} and elem.value:
                suspect_metadata = True
    if invalid_uid:
        findings.append(_finding(
            "FAIL", "INVALID_UID", "An output DICOM UID is malformed.", file_id
        ))
    if suspect_metadata:
        findings.append(_finding(
            "FAIL", "RESIDUAL_METADATA", "Potential identifying metadata remains in the output.",
            file_id,
        ))
    return findings


def _graph_findings(snapshots: list[Snapshot], *, standalone: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    instances: dict[str, list[Snapshot]] = defaultdict(list)
    series_studies: dict[str, set[str]] = defaultdict(set)
    study_patients: dict[str, set[str]] = defaultdict(set)
    patient_names: dict[str, set[str]] = defaultdict(set)
    for snap in snapshots:
        instances[snap.sop_uid].append(snap)
        series_studies[snap.series_uid].add(snap.study_uid)
        study_patients[snap.study_uid].add(snap.patient_id)
        patient_names[snap.patient_id].add(snap.patient_name)
    if any(len({s.semantic_digest for s in copies}) > 1 for copies in instances.values()):
        findings.append(_finding(
            "FAIL", "CONFLICTING_DUPLICATE", "A repeated instance UID identifies conflicting objects."
        ))
    elif any(len(copies) > 1 for copies in instances.values()):
        findings.append(_finding(
            "WARNING", "DUPLICATE_COPIES", "Identical copies of some DICOM instances are present."
        ))
    if any(len(studies) > 1 for studies in series_studies.values()):
        findings.append(_finding(
            "FAIL", "SERIES_RELATIONSHIP", "A series identifier belongs to inconsistent studies."
        ))
    if any(len(patients) > 1 for patients in study_patients.values()):
        findings.append(_finding(
            "FAIL", "STUDY_PATIENT_RELATIONSHIP", "A study belongs to inconsistent patient pseudonyms."
        ))
    if any(len(names) > 1 for names in patient_names.values()):
        findings.append(_finding(
            "FAIL", "PATIENT_PSEUDONYM", "Patient pseudonyms are inconsistent across files."
        ))
    reference_class_mismatch = False
    for snap in snapshots:
        for path, references in snap.references.items():
            if not path.endswith("/00081155"):
                continue
            reference_classes = snap.technical_uids.get(path[:-8] + "00081150", ())
            for reference in references:
                if reference in instances and reference_classes and any(
                    target.sop_class_uid not in reference_classes for target in instances[reference]
                ):
                    reference_class_mismatch = True
    if reference_class_mismatch:
        findings.append(_finding(
            "FAIL", "REFERENCE_CLASS_MISMATCH",
            "An instance reference names a different SOP class than its target."
        ))
    if standalone:
        unresolved = sum(
            reference not in instances
            for snap in snapshots for values in snap.references.values() for reference in values
        )
        if unresolved:
            findings.append(_finding(
                "WARNING", "UNRESOLVED_REFERENCES",
                "Some references point outside this directory; source comparison is required."
            ))
        elif snapshots:
            findings.append(_finding(
                "PASS", "REFERENCE_GRAPH", "Every inspected instance reference resolves in this directory."
            ))
    return findings


def validate_batch(
    scan: ScanResult,
    outputs: list[ExpectedOutput],
    uid_mapping: Mapping[str, str],
    *,
    options: Options,
    cancel: Event | None = None,
    progress: ProgressCallback | None = None,
) -> list[Finding]:
    """Verify saved output against original snapshots and the sealed batch UID map."""
    findings: list[Finding] = []
    snapshots: list[Snapshot] = []
    source_instances = {record.snapshot.sop_uid for record in scan.files}
    available_output_instances: set[str] = set()
    read_pairs: list[tuple[ExpectedOutput, Snapshot]] = []
    if not scan.files:
        findings.append(_finding(
            "FAIL", "NO_DICOM_FILES", "No source DICOM objects were supplied for comparison."
        ))
    if len(outputs) != len(scan.files) or Counter(id(output.source) for output in outputs) != Counter(
        id(record) for record in scan.files
    ):
        findings.append(_finding(
            "FAIL", "FILE_COUNT", "The output inventory does not match every input file exactly once."
        ))
    if len({output.path for output in outputs}) != len(outputs):
        findings.append(_finding(
            "FAIL", "OUTPUT_PATH_COLLISION", "Multiple source files were assigned the same output path."
        ))
    if len(set(uid_mapping.values())) != len(uid_mapping):
        findings.append(_finding(
            "FAIL", "UID_COLLISION", "The batch UID mapping contains a collision."
        ))
    if set(uid_mapping).intersection(uid_mapping.values()):
        findings.append(_finding(
            "FAIL", "UID_UNCHANGED", "An original entity UID remains in the batch UID mapping."
        ))
    patients: dict[tuple[str, ...], set[str]] = defaultdict(set)
    date_offsets: dict[tuple[str, ...], set[int]] = defaultdict(set)
    for output in outputs:
        patients[output.source.patient_key].add(output.pseudonym)
        date_offsets[output.source.patient_key].add(output.date_offset_days)
    if any(len(names) != 1 for names in patients.values()) or (
        len({output.pseudonym for output in outputs}) != len(patients)
    ):
        findings.append(_finding(
            "FAIL", "PATIENT_PSEUDONYM", "Patient pseudonyms do not preserve distinct input identities."
        ))
    if options.date_mode == "shift" and any(
        len(offsets) != 1 or 0 in offsets for offsets in date_offsets.values()
    ):
        findings.append(_finding(
            "FAIL", "DATE_POLICY", "Date shifting requires one nonzero offset per patient."
        ))
    for index, output in enumerate(outputs, 1):
        check_cancel(cancel)
        try:
            ds = read_dicom(output.path, remove_private=False)
            snapshot = capture_snapshot(ds)
        except Exception:
            findings.append(_finding(
                "FAIL", "OUTPUT_UNREADABLE", "An output file could not be safely read.",
                output.source.file_id,
            ))
            continue
        snapshots.append(snapshot)
        available_output_instances.add(snapshot.sop_uid)
        read_pairs.append((output, snapshot))
        findings.extend(_metadata_findings(
            ds, snapshot, output.source.file_id, pseudonym=output.pseudonym, options=options
        ))
        source = output.source.snapshot
        try:
            expected = {
                path: tuple(uid_mapping[uid] for uid in values)
                for path, values in source.entity_uids.items()
            }
        except KeyError:
            expected = {}
            findings.append(_finding(
                "FAIL", "UID_MAPPING_MISSING", "An entity identifier has no batch UID replacement.",
                output.source.file_id,
            ))
        if snapshot.entity_uids != expected:
            findings.append(_finding(
                "FAIL", "UID_INTEGRITY", "An entity UID or nested reference was not mapped consistently.",
                output.source.file_id,
            ))
        if snapshot.technical_uids != source.technical_uids:
            findings.append(_finding(
                "FAIL", "TECHNICAL_UID_CHANGED", "A technical DICOM identifier changed unexpectedly.",
                output.source.file_id,
            ))
        if source.transfer_syntax_uid and snapshot.transfer_syntax_uid != source.transfer_syntax_uid:
            findings.append(_finding(
                "FAIL", "TRANSFER_SYNTAX_CHANGED", "The original transfer syntax changed unexpectedly.",
                output.source.file_id,
            ))
        if snapshot.clinical != source.clinical:
            findings.append(_finding(
                "FAIL", "CLINICAL_DATA_CHANGED",
                "Pixel data, geometry, or selected clinical parameters changed unexpectedly.",
                output.source.file_id,
            ))
        expected_dates = {}
        try:
            for path, (vr, values) in source.dates.items():
                if options.date_mode == "remove" or path.endswith(("/00100030", "/00100032")):
                    shifted_values = ()
                elif vr == "TM":
                    shifted_values = values
                else:
                    shifted_values = tuple(
                        (datetime.strptime(value[:8], "%Y%m%d")
                         + timedelta(days=output.date_offset_days)).strftime("%Y%m%d") + value[8:]
                        for value in values
                    )
                expected_dates[path] = (vr, shifted_values)
            dates_match = snapshot.dates == expected_dates
        except (ValueError, OverflowError):
            dates_match = False
        if not dates_match:
            findings.append(_finding(
                "FAIL", "DATE_POLICY", "Date removal or consistent patient date shifting failed.",
                output.source.file_id,
            ))
        if progress:
            progress(Progress("Validating", index, len(outputs)))
    preexisting_missing = 0
    new_missing = 0
    for output, snapshot in read_pairs:
        for path, old_references in output.source.snapshot.references.items():
            new_references = snapshot.references.get(path, ())
            for index, old_reference in enumerate(old_references):
                if old_reference not in source_instances:
                    preexisting_missing += 1
                elif (
                    index >= len(new_references)
                    or new_references[index] != uid_mapping.get(old_reference)
                    or new_references[index] not in available_output_instances
                ):
                    new_missing += 1
    if preexisting_missing:
        findings.append(_finding(
            "WARNING", "PREEXISTING_MISSING_REFERENCES",
            "The input already referenced objects outside the selected dataset; those references "
            "were remapped but remain unresolved."
        ))
    if new_missing:
        findings.append(_finding(
            "FAIL", "BROKEN_REFERENCES", "An instance reference that resolved in the input is now broken."
        ))
    findings.extend(_graph_findings(snapshots))
    failures = {finding.code for finding in findings if finding.severity == "FAIL"}
    if not failures.intersection({
        "OUTPUT_UNREADABLE", "FILE_COUNT", "OUTPUT_PATH_COLLISION", "NO_DICOM_FILES",
    }):
        findings.append(_finding("PASS", "READABILITY", "All expected output files are readable."))
    if not failures.intersection({
        "UID_INTEGRITY", "UID_MAPPING_MISSING", "UID_COLLISION", "BROKEN_REFERENCES",
        "UID_UNCHANGED", "REFERENCE_CLASS_MISMATCH",
        "OUTPUT_UNREADABLE", "FILE_COUNT", "SERIES_RELATIONSHIP", "STUDY_PATIENT_RELATIONSHIP",
        "CONFLICTING_DUPLICATE",
        "NO_DICOM_FILES",
    }):
        findings.append(_finding(
            "PASS", "REFERENCE_GRAPH", "All instance relationships present in the input were preserved."
        ))
    if not failures.intersection({
        "CLINICAL_DATA_CHANGED", "OUTPUT_UNREADABLE", "FILE_COUNT", "NO_DICOM_FILES",
    }):
        findings.append(_finding(
            "PASS", "CLINICAL_PRESERVATION", "Pixels and selected geometry and clinical parameters are unchanged."
        ))
    if not failures.intersection({
        "FILE_META_MISMATCH", "PRIVATE_TAGS", "RESIDUAL_METADATA", "INVALID_UID",
        "RESIDUAL_FILE_METADATA", "UNSUPPORTED_OBJECT",
        "INVALID_STRUCTURE",
        "PATIENT_PSEUDONYM", "DEIDENTIFICATION_MARKERS", "TECHNICAL_UID_CHANGED",
        "TRANSFER_SYNTAX_CHANGED", "DATE_POLICY",
        "OUTPUT_UNREADABLE", "FILE_COUNT",
        "NO_DICOM_FILES",
    }):
        findings.append(_finding("PASS", "METADATA", "Implemented metadata checks passed."))
    return findings


def validate_directory(path: str | Path, recursive: bool = True) -> Report:
    """Inspect an existing output directory, without claiming source equivalence."""
    directory = Path(path)
    report = Report()
    if not directory.is_dir():
        report.findings.append(_finding("FAIL", "INVALID_DIRECTORY", "Select an existing directory."))
        return report
    files = sorted(directory.rglob("*") if recursive else directory.iterdir())
    snapshots: list[Snapshot] = []
    for candidate in files:
        if candidate.is_symlink():
            report.findings.append(_finding(
                "FAIL", "SYMLINK", "A symbolic link cannot be included in output validation."
            ))
            continue
        if not candidate.is_file():
            continue
        file_id = f"FILE-{report.input_count + 1:06d}"
        try:
            ds = read_dicom(candidate, remove_private=False)
            snapshot = capture_snapshot(ds)
        except Exception:
            if looks_like_dicom(candidate):
                report.input_count += 1
                report.findings.append(_finding(
                    "FAIL", "OUTPUT_UNREADABLE", "A DICOM file could not be safely read.", file_id
                ))
            continue
        report.input_count += 1
        report.output_count += 1
        modality = str(ds.get("Modality", "OTHER"))
        # A hostile unknown modality value must not become report content.
        modality = modality if modality in {"CT", "MR", "RTDOSE", "RTSTRUCT", "RTPLAN", "RTIMAGE",
                                            "CR", "DX", "US", "NM", "PT", "SC"} else "OTHER"
        report.modalities[modality] = report.modalities.get(modality, 0) + 1
        snapshots.append(snapshot)
        report.findings.extend(_metadata_findings(ds, snapshot, file_id))
    if not report.input_count:
        report.findings.append(_finding("FAIL", "NO_DICOM_FILES", "No readable DICOM objects were found."))
    graph = _graph_findings(snapshots, standalone=True)
    if any(f.severity == "FAIL" for f in report.findings + graph):
        graph = [f for f in graph if f.severity != "PASS"]
    report.findings.extend(graph)
    report.findings.append(_finding(
        "WARNING", "NO_SOURCE_COMPARISON",
        "No source dataset was provided: original relationships, patient consistency, and clinical "
        "preservation cannot be verified. Privacy review remains required."
    ))
    if not any(f.severity == "FAIL" for f in report.findings):
        report.status = "COMPLETED"
        report.findings.append(_finding(
            "PASS", "READABILITY", "The inspected output DICOM files are readable."
        ))
    return report

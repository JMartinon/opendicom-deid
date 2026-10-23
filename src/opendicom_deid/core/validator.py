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

from .equipment_text import cleaned_equipment_values
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
from .reference_classes import reference_class_findings, reference_class_repairs
from .report import Report
from .rt_text import clean_rt_values
from .sop_classes import (
    SUPPORTED_SOP_CLASSES,
    VIEWRAY_PLAN_REVIEW_REQUIRED,
    VIEWRAY_PLAN_WARNING,
    VIEWRAY_RT_PLAN_STORAGE,
    modality_error,
    modality_label,
    unsupported_object_message,
)
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
    if problem := structural_error(ds):
        findings.append(_finding(
            "FAIL", "INVALID_STRUCTURE", problem, file_id
        ))
    date_mode = options.date_mode if options else (
        "shift" if ds.get("LongitudinalTemporalInformationModified") == "MODIFIED" else "remove"
    )
    policy_issues = metadata_issues(
        ds, pseudonym=pseudonym, date_mode=date_mode,
        text_policy=options.text_policy if options else None,
    )
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
    if snapshot.sop_class_uid not in SUPPORTED_SOP_CLASSES:
        findings.append(_finding(
            "FAIL", "UNSUPPORTED_OBJECT", unsupported_object_message(ds), file_id
        ))
    elif problem := modality_error(ds):
        findings.append(_finding("FAIL", "MODALITY", problem, file_id))
    if snapshot.sop_class_uid == VIEWRAY_RT_PLAN_STORAGE and options is None:
        # Batch reports already include the source scan warning. Independent
        # validation must carry the same limitation when no scan is available.
        findings.append(_finding("WARNING", "VIEWRAY_PRIVATE_PLAN", VIEWRAY_PLAN_WARNING, file_id))
    elif snapshot.sop_class_uid == VIEWRAY_RT_PLAN_STORAGE and not options.allow_viewray_public_plan:
        findings.append(_finding(
            "FAIL", "VIEWRAY_PLAN_REVIEW_REQUIRED", VIEWRAY_PLAN_REVIEW_REQUIRED, file_id
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


def _graph_findings(
    snapshots: list[Snapshot], *, standalone: bool = False, labels: list[str] | None = None,
) -> list[Finding]:
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
    labels = labels if labels is not None else [f"File {i:06d}" for i in range(1, len(snapshots) + 1)]
    findings.extend(reference_class_findings(list(zip(labels, snapshots, strict=True))))
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
    identifiers = frozenset(token for record in scan.files for token in record.snapshot.identifiers)
    class_repairs = reference_class_repairs(scan.files, enabled=options.allow_viewray_public_plan)
    equipment_redacted = False
    rt_text_redacted = False
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
        expected_equipment = {
            path: cleaned_equipment_values(values, identifiers)
            for path, values in source.equipment_text.items()
        }
        equipment_redacted |= expected_equipment != source.equipment_text
        if options.text_policy == "clinical":
            expected_rt_text = {
                path: (vr, keyword, clean_rt_values(values, identifiers))
                for path, (vr, keyword, values) in source.rt_text.items()
            }
            rt_text_redacted |= expected_rt_text != source.rt_text
            if snapshot.rt_text != expected_rt_text:
                findings.append(_finding(
                    "FAIL", "RT_TEXT_CHANGED",
                    "Clinical RT labels or descriptions do not match the expected identifier removal.",
                    output.source.file_id,
                ))
        expected_beams = source.neutral_beam_names if options.text_policy == "strict" else {
            path: clean_rt_values(values, identifiers) for path, values in source.beam_names.items()
        }
        if snapshot.equipment_text != expected_equipment or snapshot.beam_names != expected_beams:
            findings.append(_finding(
                "FAIL", "EQUIPMENT_TEXT_CHANGED",
                "Equipment text or beam labels do not match the expected privacy transformation.",
                output.source.file_id,
            ))
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
        expected_technical = dict(source.technical_uids)
        expected_technical.update({
            path: (sop_class,) for path, sop_class in class_repairs.get(output.source.file_id, {}).items()
        })
        if snapshot.technical_uids != expected_technical:
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
    if any(any(values) for snap in snapshots for values in snap.equipment_text.values()):
        findings.append(_finding(
            "WARNING", "EQUIPMENT_TEXT_REVIEW",
            "Selected equipment text was retained. Review locally chosen machine, model, "
            "software, fluence and material labels before sharing; known-identifier screening "
            "does not establish that custom text is anonymous."
        ))
    if equipment_redacted:
        findings.append(_finding(
            "WARNING", "EQUIPMENT_TEXT_REDACTED",
            "Equipment text matching known source identifiers was neutralized. "
            "Check affected equipment mappings locally before downstream analysis."
        ))
    if options.text_policy == "clinical" and any(snap.rt_text for snap in snapshots):
        findings.append(_finding(
            "WARNING", "CLINICAL_TEXT_REVIEW",
            "Clinical RT names and descriptions were retained with known identifiers removed. "
            "Review free text for identities absent from the source metadata before sharing."
        ))
    if rt_text_redacted:
        findings.append(_finding(
            "WARNING", "CLINICAL_TEXT_REDACTED",
            "Known patient, staff, institution or source identifiers were removed from clinical RT text."
        ))
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
    findings.extend(_graph_findings(
        snapshots, labels=[output.source.file_id for output, _ in read_pairs],
    ))
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
        "CLINICAL_DATA_CHANGED", "EQUIPMENT_TEXT_CHANGED", "RT_TEXT_CHANGED",
        "OUTPUT_UNREADABLE", "FILE_COUNT", "NO_DICOM_FILES",
    }):
        findings.append(_finding(
            "PASS", "CLINICAL_PRESERVATION",
            "Pixels, retained RT sequence structure and selected clinical parameters are unchanged; "
            "retained text matches the selected privacy policy."
        ))
    if not failures.intersection({
        "FILE_META_MISMATCH", "PRIVATE_TAGS", "RESIDUAL_METADATA", "INVALID_UID",
        "RESIDUAL_FILE_METADATA", "UNSUPPORTED_OBJECT", "MODALITY",
        "INVALID_STRUCTURE", "VIEWRAY_PLAN_REVIEW_REQUIRED",
        "PATIENT_PSEUDONYM", "DEIDENTIFICATION_MARKERS", "TECHNICAL_UID_CHANGED",
        "REFERENCE_CLASS_MISMATCH",
        "TRANSFER_SYNTAX_CHANGED", "DATE_POLICY",
        "EQUIPMENT_TEXT_CHANGED",
        "RT_TEXT_CHANGED",
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
    labels: list[str] = []
    retained_clinical_text = False
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
        modality = modality_label(ds)
        report.modalities[modality] = report.modalities.get(modality, 0) + 1
        snapshots.append(snapshot)
        labels.append(file_id)
        retained_clinical_text |= bool(snapshot.rt_text) and (
            "Clinical RT text retained" in str(ds.get("DeidentificationMethod", ""))
        )
        report.findings.extend(_metadata_findings(ds, snapshot, file_id))
    if not report.input_count:
        report.findings.append(_finding("FAIL", "NO_DICOM_FILES", "No readable DICOM objects were found."))
    if any(any(values) for snap in snapshots for values in snap.equipment_text.values()):
        report.findings.append(_finding(
            "WARNING", "EQUIPMENT_TEXT_REVIEW",
            "Selected equipment text remains. Without the originals, known-identifier screening "
            "cannot be checked. Review custom equipment labels before sharing."
        ))
    if retained_clinical_text:
        report.findings.append(_finding(
            "WARNING", "CLINICAL_TEXT_REVIEW",
            "Clinical RT text remains. Without the originals, known-identifier screening "
            "cannot be checked. Review retained names and descriptions before sharing."
        ))
    graph = _graph_findings(snapshots, standalone=True, labels=labels)
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

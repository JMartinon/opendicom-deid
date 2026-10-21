"""Synthetic regression coverage for the narrowly allowed ViewRay reference repair."""

from __future__ import annotations

import copy
import json
import shutil

import pydicom
import pytest
from conftest import make_dataset, reference, write_dataset
from pydicom.dataset import Dataset
from pydicom.uid import CTImageStorage, MRImageStorage, RTPlanStorage, generate_uid
from test_viewray_plan import _viewray_plan

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.inspection import capture_snapshot
from opendicom_deid.core.models import ExpectedOutput, Options
from opendicom_deid.core.reference_classes import reference_class_repairs
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.sop_classes import VIEWRAY_RT_PLAN_STORAGE
from opendicom_deid.core.validator import validate_batch, validate_directory

PUBLIC_PLAN = Options(allow_viewray_public_plan=True)
PLAN_CLASS_PATH = "/300C0002[0]/00081150"


def _stale_reference(plan):
    item = reference(plan)
    item.ReferencedSOPClassUID = RTPlanStorage
    return item


def _pair(*, stale=True, **plan_kwargs):
    plan = _viewray_plan(**plan_kwargs)
    dose = make_dataset("RTDOSE")
    dose.ReferencedRTPlanSequence = [
        _stale_reference(plan) if stale else reference(plan)
    ]
    return plan, dose


def _write_sources(tmp_path, *datasets):
    source = tmp_path / "source"
    for index, dataset in enumerate(datasets, 1):
        dataset.InstanceNumber = index
        write_dataset(dataset, source / f"{index:02d}-synthetic.dcm")
    return source


def _validation_inputs(source, destination):
    scan = scan_directory(source, allow_viewray_public_plan=True)
    assert scan.ok, scan.findings
    report = process_batch(source, destination, options=PUBLIC_PLAN)
    assert report.ok, report.to_dict()
    saved = {
        int(dataset.InstanceNumber): (path, dataset)
        for path in destination.rglob("*.dcm")
        for dataset in [pydicom.dcmread(path)]
    }
    outputs, mapping = [], {}
    for record in scan.files:
        source_dataset = pydicom.dcmread(record.path)
        path, dataset = saved[int(source_dataset.InstanceNumber)]
        snapshot = capture_snapshot(dataset)
        for tag_path, originals in record.snapshot.entity_uids.items():
            mapping.update(zip(originals, snapshot.entity_uids[tag_path], strict=True))
        outputs.append(ExpectedOutput(record, path, str(dataset.PatientID), 0))
    return scan, outputs, mapping, report


def test_linked_viewray_references_repaired_without_changing_other_content(tmp_path):
    plan, dose = _pair()
    image, ct = make_dataset("RTIMAGE"), make_dataset("CT")
    image.ReferencedRTPlanSequence = [reference(plan)]
    series = Dataset()
    series.SeriesInstanceUID = plan.SeriesInstanceUID
    series.ReferencedInstanceSequence = [_stale_reference(plan)]
    image.ReferencedSeriesSequence = [series]
    image.ReferencedImageSequence = [reference(ct)]
    plan.ReferencedDoseSequence = [reference(dose)]
    source = _write_sources(tmp_path, plan, dose, image, ct)
    original_bytes = {path: path.read_bytes() for path in source.glob("*.dcm")}
    destination = tmp_path / "output"

    scan, outputs, mapping, report = _validation_inputs(source, destination)

    assert report.output_count == 4
    repairs = [f for f in scan.findings if f.code == "VIEWRAY_REFERENCE_CLASS_REPAIR"]
    assert {f.file_id for f in repairs} == {"File 000002", "File 000003"}
    assert all(f.severity == "WARNING" for f in repairs)
    assert not any(f.code == "REFERENCE_CLASS_MISMATCH" for f in report.findings)
    assert all(path.read_bytes() == contents for path, contents in original_bytes.items())
    assert len(set(mapping.values())) == len(mapping)
    assert not set(mapping).intersection(mapping.values())
    expected_changes = {
        "File 000002": {PLAN_CLASS_PATH},
        "File 000003": {"/00081115[0]/0008114A[0]/00081150"},
    }
    for output in outputs:
        dataset = pydicom.dcmread(output.path)
        before, after = output.source.snapshot, capture_snapshot(dataset)
        expected_technical = dict(before.technical_uids)
        for path in expected_changes.get(output.source.file_id, set()):
            expected_technical[path] = (VIEWRAY_RT_PLAN_STORAGE,)
        assert after.technical_uids == expected_technical
        assert after.clinical == before.clinical
        assert after.sop_class_uid == before.sop_class_uid
        assert dataset.file_meta.MediaStorageSOPClassUID == before.sop_class_uid
        assert after.references == {
            path: tuple(mapping[uid] for uid in values)
            for path, values in before.references.items()
        }
    standalone = validate_directory(destination)
    assert standalone.ok, standalone.to_dict()


def test_already_consistent_private_plan_references_are_unchanged(tmp_path):
    source = _write_sources(tmp_path, *_pair(stale=False))
    scan, outputs, _, report = _validation_inputs(source, tmp_path / "output")
    assert not reference_class_repairs(scan.files, enabled=True)
    assert not any(f.code == "VIEWRAY_REFERENCE_CLASS_REPAIR" for f in report.findings)
    for output in outputs:
        assert capture_snapshot(pydicom.dcmread(output.path)).technical_uids == (
            output.source.snapshot.technical_uids
        )


def test_reference_correction_requires_public_plan_opt_in(tmp_path):
    source = _write_sources(tmp_path, *_pair())
    enabled_scan = scan_directory(source, allow_viewray_public_plan=True)
    assert not reference_class_repairs(enabled_scan.files, enabled=False)
    destination = tmp_path / "output"
    report = process_batch(source, destination)
    assert not report.ok
    assert report.output_count == 0
    assert not destination.exists()
    assert any(f.code == "VIEWRAY_PLAN_REVIEW_REQUIRED" for f in report.findings)
    assert not any(f.code == "VIEWRAY_REFERENCE_CLASS_REPAIR" for f in report.findings)


@pytest.mark.parametrize("scope", ["patient", "issuer", "qualifier"])
def test_viewray_repair_refuses_different_patient_identity_scope(tmp_path, scope):
    plan, dose = _pair()
    if scope == "patient":
        plan.PatientID = "DIFFERENT-SYNTHETIC-PATIENT"
    elif scope == "issuer":
        plan.IssuerOfPatientID = "SYNTHETIC-ISSUER-A"
        dose.IssuerOfPatientID = "SYNTHETIC-ISSUER-B"
    else:
        qualifier = Dataset()
        qualifier.LocalNamespaceEntityID = "SYNTHETIC-SCOPE"
        plan.IssuerOfPatientIDQualifiersSequence = [qualifier]
    source = _write_sources(tmp_path, plan, dose)
    scan = scan_directory(source, allow_viewray_public_plan=True)
    assert not scan.ok
    assert not reference_class_repairs(scan.files, enabled=True)
    assert any(f.code == "REFERENCE_CLASS_MISMATCH" for f in scan.findings)
    report = process_batch(source, tmp_path / "output", options=PUBLIC_PLAN)
    assert not report.ok
    assert report.output_count == 0
    assert not (tmp_path / "output").exists()


def test_same_patient_reference_across_studies_can_be_repaired(tmp_path):
    plan, dose = _pair()
    assert plan.StudyInstanceUID != dose.StudyInstanceUID
    source = _write_sources(tmp_path, plan, dose)
    scan, _, _, report = _validation_inputs(source, tmp_path / "output")
    assert reference_class_repairs(scan.files, enabled=True)
    assert any(f.code == "VIEWRAY_REFERENCE_CLASS_REPAIR" for f in report.findings)


@pytest.mark.parametrize("mismatch", ["reverse", "other_standard", "unknown", "ct_target"])
def test_other_class_mismatches_fail_during_source_scan(tmp_path, mismatch):
    plan, dose = _pair()
    ref = dose.ReferencedRTPlanSequence[0]
    if mismatch == "reverse":
        plan.SOPClassUID = RTPlanStorage
        plan.file_meta.MediaStorageSOPClassUID = RTPlanStorage
        ref.ReferencedSOPClassUID = VIEWRAY_RT_PLAN_STORAGE
    elif mismatch == "other_standard":
        ref.ReferencedSOPClassUID = MRImageStorage
    elif mismatch == "unknown":
        ref.ReferencedSOPClassUID = "2.25.1173142719843231961983533"
    else:
        plan = make_dataset("CT")
        ref.ReferencedSOPInstanceUID = plan.SOPInstanceUID
    source = _write_sources(tmp_path, plan, dose)
    scan = scan_directory(source, allow_viewray_public_plan=True)
    assert not scan.ok
    assert not reference_class_repairs(scan.files, enabled=True)
    mismatches = [f for f in scan.findings if f.code == "REFERENCE_CLASS_MISMATCH"]
    assert mismatches
    assert all(f.severity == "FAIL" and f.file_id == "File 000002" for f in mismatches)
    assert any(PLAN_CLASS_PATH in f.message for f in mismatches)
    report = process_batch(source, tmp_path / "output", options=PUBLIC_PLAN)
    assert not report.ok
    assert report.output_count == 0
    assert not (tmp_path / "output").exists()
    diagnostics = json.dumps([f.__dict__ for f in scan.findings] + report.to_dict()["findings"])
    for value in (
        str(plan.SOPInstanceUID), str(dose.SOPInstanceUID), str(plan.StudyInstanceUID),
        str(plan.PatientName), str(plan.PatientID), str(source), "02-synthetic.dcm",
        "2.25.1173142719843231961983533",
    ):
        assert value not in diagnostics


@pytest.mark.parametrize("multi_value", ["class", "instance"])
def test_multivalued_reference_pair_is_not_repaired(tmp_path, multi_value):
    plan, dose = _pair()
    ref = dose.ReferencedRTPlanSequence[0]
    if multi_value == "class":
        ref.ReferencedSOPClassUID = [RTPlanStorage, CTImageStorage]
    else:
        ref.ReferencedSOPInstanceUID = [plan.SOPInstanceUID, generate_uid()]
    source = _write_sources(tmp_path, plan, dose)
    scan = scan_directory(source, allow_viewray_public_plan=True)
    assert not scan.ok
    assert not reference_class_repairs(scan.files, enabled=True)
    assert any(f.code == "REFERENCE_CLASS_MISMATCH" for f in scan.findings)
    assert not any(f.code == "VIEWRAY_REFERENCE_CLASS_REPAIR" for f in scan.findings)


def test_class_and_instance_in_different_items_are_not_paired_for_repair(tmp_path):
    plan, dose = _pair()
    item = dose.ReferencedRTPlanSequence[0]
    del item.ReferencedSOPClassUID
    separate = Dataset()
    separate.ReferencedSOPClassUID = RTPlanStorage
    dose.ReferencedRTPlanSequence.append(separate)
    source = _write_sources(tmp_path, plan, dose)
    scan = scan_directory(source, allow_viewray_public_plan=True)
    assert not reference_class_repairs(scan.files, enabled=True)
    assert not any(f.code == "VIEWRAY_REFERENCE_CLASS_REPAIR" for f in scan.findings)


def test_unresolved_reference_is_remapped_without_guessing_its_class(tmp_path):
    plan, dose = _pair()
    ref = dose.ReferencedRTPlanSequence[0]
    missing_uid = generate_uid()
    ref.ReferencedSOPInstanceUID = missing_uid
    source = _write_sources(tmp_path, plan, dose)
    scan, outputs, mapping, report = _validation_inputs(source, tmp_path / "output")
    assert not reference_class_repairs(scan.files, enabled=True)
    assert any(f.code == "PREEXISTING_MISSING_REFERENCES" for f in report.findings)
    assert not any(f.code == "VIEWRAY_REFERENCE_CLASS_REPAIR" for f in report.findings)
    saved_dose = pydicom.dcmread(outputs[1].path)
    saved_ref = saved_dose.ReferencedRTPlanSequence[0]
    assert saved_ref.ReferencedSOPClassUID == RTPlanStorage
    assert saved_ref.ReferencedSOPInstanceUID == mapping[missing_uid]
    assert saved_ref.ReferencedSOPInstanceUID != missing_uid


def test_identical_duplicate_targets_allow_unambiguous_repair(tmp_path):
    plan, dose = _pair()
    source = _write_sources(tmp_path, plan, dose)
    shutil.copyfile(source / "01-synthetic.dcm", source / "03-copy.dcm")
    scan = scan_directory(source, allow_viewray_public_plan=True)
    assert scan.ok, scan.findings
    assert reference_class_repairs(scan.files, enabled=True)
    destination = tmp_path / "output"
    report = process_batch(source, destination, options=PUBLIC_PLAN)
    assert report.ok, report.to_dict()
    assert report.output_count == 3
    saved = [pydicom.dcmread(path) for path in destination.rglob("*.dcm")]
    saved_plans = [dataset for dataset in saved if dataset.Modality == "RTPLAN"]
    assert len(saved_plans) == 2
    assert saved_plans[0].SOPInstanceUID == saved_plans[1].SOPInstanceUID
    saved_dose = next(dataset for dataset in saved if dataset.Modality == "RTDOSE")
    assert saved_dose.ReferencedRTPlanSequence[0].ReferencedSOPClassUID == VIEWRAY_RT_PLAN_STORAGE
    assert saved_dose.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID == saved_plans[0].SOPInstanceUID


def test_private_only_conflicting_duplicate_target_prevents_repair(tmp_path):
    plan, dose = _pair()
    source = _write_sources(tmp_path, plan, dose)
    conflicting = copy.deepcopy(plan)
    conflicting.add_new(0x00110010, "LO", "SYNTHETIC PRIVATE CREATOR")
    conflicting.add_new(0x00111001, "LO", "SYNTHETIC DIFFERENT CONTENT")
    write_dataset(conflicting, source / "03-conflict.dcm")
    scan = scan_directory(source, allow_viewray_public_plan=True)
    assert not scan.ok
    assert not reference_class_repairs(scan.files, enabled=True)
    assert any(f.code == "CONFLICTING_DUPLICATE" for f in scan.findings)
    assert not any(f.code == "VIEWRAY_REFERENCE_CLASS_REPAIR" for f in scan.findings)
    report = process_batch(source, tmp_path / "output", options=PUBLIC_PLAN)
    assert not report.ok
    assert report.output_count == 0
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("stale_input", [False, True])
@pytest.mark.parametrize("tamper_class", [RTPlanStorage, MRImageStorage])
def test_output_reference_mismatch_is_never_silently_repaired(
    tmp_path, stale_input, tamper_class
):
    source = _write_sources(tmp_path, *_pair(stale=stale_input))
    destination = tmp_path / "output"
    scan, outputs, mapping, _ = _validation_inputs(source, destination)
    path = outputs[1].path
    saved = pydicom.dcmread(path)
    saved.ReferencedRTPlanSequence[0].ReferencedSOPClassUID = tamper_class
    write_dataset(saved, path)
    unchanged_bytes = path.read_bytes()

    findings = validate_batch(scan, outputs, mapping, options=PUBLIC_PLAN)

    assert any(f.code == "TECHNICAL_UID_CHANGED" and f.severity == "FAIL" for f in findings)
    assert any(f.code == "REFERENCE_CLASS_MISMATCH" and f.severity == "FAIL" for f in findings)
    assert not any(f.code == "REFERENCE_GRAPH" and f.severity == "PASS" for f in findings)
    assert path.read_bytes() == unchanged_bytes
    standalone = validate_directory(destination)
    assert not standalone.ok
    assert any(f.code == "REFERENCE_CLASS_MISMATCH" for f in standalone.findings)


def test_output_plan_is_not_reclassified_as_standard_storage(tmp_path):
    source = _write_sources(tmp_path, *_pair())
    scan, outputs, mapping, _ = _validation_inputs(source, tmp_path / "output")
    path = outputs[0].path
    saved = pydicom.dcmread(path)
    saved.SOPClassUID = RTPlanStorage
    saved.file_meta.MediaStorageSOPClassUID = RTPlanStorage
    write_dataset(saved, path)
    findings = validate_batch(scan, outputs, mapping, options=PUBLIC_PLAN)
    assert any(f.code == "TECHNICAL_UID_CHANGED" and f.severity == "FAIL" for f in findings)
    assert any(f.code == "REFERENCE_CLASS_MISMATCH" and f.severity == "FAIL" for f in findings)

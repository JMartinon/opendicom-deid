"""Synthetic coverage for useful equipment text and neutral beam labels."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pydicom
import pytest
from conftest import reference
from pydicom.dataset import Dataset

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.inspection import capture_snapshot
from opendicom_deid.core.models import ExpectedOutput, Options
from opendicom_deid.core.policy import metadata_issues, scrub_metadata
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.validator import validate_batch, validate_directory


def _scrub(dataset):
    mapping = {
        uid: f"2.25.{index}"
        for index, uid in enumerate(dict.fromkeys(
            uid for values in capture_snapshot(dataset).entity_uids.values() for uid in values
        ), 1)
    }
    scrub_metadata(dataset, pseudonym="SUBJECT-TEST", uid_mapping=mapping)


def _beam(number, name):
    beam = Dataset()
    beam.BeamNumber = number
    beam.BeamName = name
    beam.Manufacturer = "Acme Medical"
    beam.ManufacturerModelName = "Linac Pro"
    beam.TreatmentMachineName = "LINAC-A"
    beam.BeamType = "STATIC"
    beam.RadiationType = "PHOTON"
    beam.TreatmentDeliveryType = "TREATMENT"
    beam.NumberOfControlPoints = 2
    beam.FinalCumulativeMetersetWeight = 1
    points = []
    for index in range(2):
        point = Dataset()
        point.ControlPointIndex = index
        point.CumulativeMetersetWeight = index
        point.GantryAngle = 90
        point.NominalBeamEnergy = 6
        point.IsocenterPosition = [4.5, 7, -1.5]
        device = Dataset()
        device.RTBeamLimitingDeviceType = "MLCX"
        device.LeafJawPositions = [-10, -8, 10, 8]
        point.BeamLimitingDevicePositionSequence = [device]
        points.append(point)
    beam.ControlPointSequence = points
    return beam


@pytest.mark.parametrize("keyword,value", [
    ("Manufacturer", "Acme Medical"),
    ("ManufacturerModelName", "Linac Pro"),
    ("SoftwareVersions", ["5.5.4.14", "Control-2.1"]),
    ("TreatmentMachineName", "LINAC-A"),
    ("FluenceModeID", "FFF"),
    ("MaterialID", "WATER"),
])
def test_reviewed_equipment_fields_survive_at_root_and_inside_sequences(
    dicom_factory, keyword, value
):
    dataset = dicom_factory("RTPLAN")
    setattr(dataset, keyword, deepcopy(value))
    beam = _beam(1, "Patient-specific beam name")
    setattr(beam, keyword, deepcopy(value))
    dataset.BeamSequence = [beam]
    _scrub(dataset)
    assert getattr(dataset, keyword) == value
    assert getattr(dataset.BeamSequence[0], keyword) == value
    assert not metadata_issues(dataset, pseudonym="SUBJECT-TEST")


@pytest.mark.parametrize("identity_keyword,identity,equipment_keyword,value", [
    ("PatientID", "AB-12345", "Manufacturer", "ACME ab 12345"),
    ("PatientName", "García^Lucía", "ManufacturerModelName", "Model GARCIA"),
    ("PatientName", "García^Lucía", "TreatmentMachineName", "lucia-LINAC"),
    ("StudyID", "STUDY-4927", "TreatmentMachineName", "study 4927"),
    ("AccessionNumber", "ACC-84017", "MaterialID", "acc.84017"),
    ("PatientBirthDate", "19760203", "SoftwareVersions", "1976-02-03"),
    ("ReferringPhysicianName", "Fictional^Clinician", "Manufacturer", "FICTIONAL"),
    ("PatientID", "42", "TreatmentMachineName", "LINAC-42"),
    ("AccessionNumber", "42", "TreatmentMachineName", "LINAC-42"),
    ("ClinicalTrialSubjectID", "TRIAL-32871", "Manufacturer", "ACME TRIAL 32871"),
    ("ClinicalTrialSubjectReadingID", "READ-47218", "Manufacturer", "ACME read47218"),
])
def test_known_identity_variants_are_removed_from_equipment_text(
    dicom_factory, identity_keyword, identity, equipment_keyword, value
):
    dataset = dicom_factory()
    setattr(dataset, identity_keyword, identity)
    setattr(dataset, equipment_keyword, value)
    _scrub(dataset)
    assert getattr(dataset, equipment_keyword) == "ANONYMIZED"
    assert dataset.PatientName == dataset.PatientID == "SUBJECT-TEST"
    assert dataset.PatientBirthDate == ""
    assert not metadata_issues(dataset, pseudonym="SUBJECT-TEST")


@pytest.mark.parametrize("keyword", [
    "SOPInstanceUID", "StudyInstanceUID", "ReferencedSOPInstanceUID",
])
def test_known_entity_and_reference_uids_are_removed_from_equipment_text(dicom_factory, keyword):
    dataset = dicom_factory()
    original_uid = "2.25.1234567891011"
    setattr(dataset, keyword, original_uid)
    dataset.Manufacturer = f"ACME {original_uid}"
    _scrub(dataset)
    assert dataset.Manufacturer == "ANONYMIZED"
    assert str(getattr(dataset, keyword)) != original_uid
    assert not metadata_issues(dataset, pseudonym="SUBJECT-TEST")


def test_technical_sop_class_uid_is_not_a_patient_identifier(dicom_factory):
    dataset = dicom_factory()
    original_class = str(dataset.SOPClassUID)
    original_manufacturer = f"ACME {original_class}"
    dataset.Manufacturer = original_manufacturer
    _scrub(dataset)
    assert dataset.Manufacturer == original_manufacturer
    assert str(dataset.SOPClassUID) == original_class
    assert not metadata_issues(dataset, pseudonym="SUBJECT-TEST")


def test_short_identifiers_do_not_destroy_versions_or_incidental_substrings(dicom_factory):
    dataset = dicom_factory()
    dataset.StudyID = "1"
    dataset.PatientName = "Li^Bo"
    dataset.SoftwareVersions = ["5.5.4.14", "Control-2.1"]
    dataset.ManufacturerModelName = "Linac Bolt"
    dataset.TreatmentMachineName = "LI"
    _scrub(dataset)
    assert list(dataset.SoftwareVersions) == ["5.5.4.14", "Control-2.1"]
    assert dataset.ManufacturerModelName == "Linac Bolt"
    assert dataset.TreatmentMachineName == "ANONYMIZED"


def test_multivalued_equipment_text_redacts_only_affected_values(dicom_factory):
    dataset = dicom_factory()
    dataset.PatientID = "CASE-24018"
    dataset.SoftwareVersions = ["5.5.4.14", "case_24018", "Control-2.1"]
    _scrub(dataset)
    assert list(dataset.SoftwareVersions) == ["5.5.4.14", "ANONYMIZED", "Control-2.1"]


@pytest.mark.parametrize("number,name,expected", [
    (1, "Original patient beam", "BEAM-1"),
    (17, "Arc with identity", "BEAM-17"),
    (1, "", ""),
    (None, "Unnumbered patient beam", "ANONYMIZED"),
    (0, "Zero beam", "BEAM-0"),
    (-2, "Negative beam", "ANONYMIZED"),
])
def test_beam_names_are_neutral_and_linked_to_valid_beam_numbers(
    dicom_factory, number, name, expected
):
    dataset = dicom_factory("RTPLAN")
    beam = Dataset()
    if number is not None:
        beam.BeamNumber = number
    beam.BeamName = name
    dataset.BeamSequence = [beam]
    _scrub(dataset)
    assert dataset.BeamSequence[0].BeamName == expected
    assert dataset.BeamSequence[0].get("BeamNumber") == number
    assert not metadata_issues(dataset, pseudonym="SUBJECT-TEST")


def test_nested_identifiers_are_collected_before_any_text_is_changed(dicom_factory):
    dataset = dicom_factory("RTPLAN")
    dataset.PatientID = "CASE-52018"
    dataset.ManufacturerModelName = "MODEL Nested-59382"
    beam = _beam(1, "Label to neutralize")
    beam.PatientID = "NESTED-59382"
    beam.TreatmentMachineName = "case 52018"
    dataset.BeamSequence = [beam]
    _scrub(dataset)
    assert dataset.ManufacturerModelName == "ANONYMIZED"
    assert dataset.BeamSequence[0].TreatmentMachineName == "ANONYMIZED"
    assert dataset.BeamSequence[0].PatientID == "SUBJECT-TEST"


def test_equipment_exception_does_not_preserve_serials_institutions_or_free_text(dicom_factory):
    dataset = dicom_factory("RTPLAN")
    dataset.DeviceSerialNumber = "SERIAL-7721"
    dataset.StationName = "SITE-A"
    dataset.InstitutionName = "Example Hospital"
    dataset.InstitutionAddress = "Example Avenue"
    dataset.RTPlanName = "Identity in plan name"
    dataset.RTPlanLabel = "Identity label"
    dataset.BeamSequence = [_beam(1, "Identity in beam name")]
    dataset.BeamSequence[0].DeviceSerialNumber = "SERIAL-7721"
    _scrub(dataset)
    for keyword in (
        "DeviceSerialNumber", "StationName", "InstitutionName", "InstitutionAddress",
        "RTPlanName", "RTPlanLabel",
    ):
        assert str(dataset.get(keyword, "")) in {"", "ANONYMIZED"}
    assert str(dataset.BeamSequence[0].get("DeviceSerialNumber", "")) in {"", "ANONYMIZED"}
    assert dataset.BeamSequence[0].BeamName == "BEAM-1"
    assert not metadata_issues(dataset, pseudonym="SUBJECT-TEST")


def test_batch_uses_identifiers_from_later_files_and_other_patients(
    tmp_path, dicom_factory, dicom_writer
):
    source, destination = tmp_path / "source", tmp_path / "output"
    first = dicom_factory(patient_id="CASE-81020", patient_name="Fictional^First")
    first.InstanceNumber = 1
    first.Manufacturer = "Acme Medical"
    first.ManufacturerModelName = "MODEL case 93762"
    first.TreatmentMachineName = "LINAC Moriya"
    second = dicom_factory(patient_id="CASE-93762", patient_name="Imaginary^Second")
    second.InstanceNumber = 2
    nested = reference(first)
    nested.ReferringPhysicianName = "Moriya^Invented"
    second.ReferencedImageSequence = [nested]
    second.ManufacturerModelName = "Linac Pro"
    original_paths = [
        dicom_writer(first, source / "a.dcm"),
        dicom_writer(second, source / "b.dcm"),
    ]
    original_hashes = [hashlib.sha256(path.read_bytes()).digest() for path in original_paths]
    report = process_batch(source, destination)
    assert report.ok, report.to_dict()
    saved = {
        int(dataset.InstanceNumber): dataset
        for dataset in (pydicom.dcmread(path) for path in destination.rglob("*.dcm"))
    }
    assert saved[1].Manufacturer == "Acme Medical"
    assert saved[1].ManufacturerModelName == saved[1].TreatmentMachineName == "ANONYMIZED"
    assert saved[2].ManufacturerModelName == "Linac Pro"
    assert saved[1].PatientID != saved[2].PatientID
    assert saved[2].ReferencedImageSequence[0].ReferencedSOPInstanceUID == saved[1].SOPInstanceUID
    assert [hashlib.sha256(path.read_bytes()).digest() for path in original_paths] == original_hashes
    assert any(
        f.severity == "WARNING" and f.code == "EQUIPMENT_TEXT_REDACTED"
        for f in report.findings
    )
    serialized = json.dumps(report.to_dict())
    for sensitive in (
        "CASE-81020", "CASE-93762", "Fictional", "Moriya", "case 93762", str(source),
    ):
        assert sensitive not in serialized


def test_saved_plan_keeps_equipment_numbers_pixels_and_references(
    tmp_path, dicom_factory, dicom_writer
):
    source, destination = tmp_path / "source", tmp_path / "output"
    image = dicom_factory()
    plan = dicom_factory("RTPLAN")
    plan.Manufacturer = "Acme Medical"
    plan.ManufacturerModelName = "Linac Pro"
    plan.SoftwareVersions = ["5.5.4.14", "Control-2.1"]
    plan.BeamSequence = [_beam(1, "Patient arc A"), _beam(2, "Patient arc B")]
    plan.ReferencedImageSequence = [reference(image)]
    beam_reference = Dataset()
    beam_reference.ReferencedBeamNumber = 2
    beam_reference.BeamMeterset = 147.5
    fraction = Dataset()
    fraction.FractionGroupNumber = 1
    fraction.NumberOfFractionsPlanned = 3
    fraction.NumberOfBeams = 2
    fraction.ReferencedBeamSequence = [beam_reference]
    plan.FractionGroupSequence = [fraction]
    for index, dataset in enumerate((image, plan), 1):
        dataset.InstanceNumber = index
        dicom_writer(dataset, source / f"{index}.dcm")
    report = process_batch(source, destination)
    assert report.ok, report.to_dict()
    saved = {
        str(dataset.Modality): dataset
        for dataset in (pydicom.dcmread(path) for path in destination.rglob("*.dcm"))
    }
    assert saved["CT"].PixelData == image.PixelData
    assert capture_snapshot(saved["CT"]).clinical == capture_snapshot(image).clinical
    assert capture_snapshot(saved["RTPLAN"]).clinical == capture_snapshot(plan).clinical
    assert saved["RTPLAN"].Manufacturer == plan.Manufacturer
    assert saved["RTPLAN"].ManufacturerModelName == plan.ManufacturerModelName
    assert saved["RTPLAN"].SoftwareVersions == plan.SoftwareVersions
    assert [beam.BeamName for beam in saved["RTPLAN"].BeamSequence] == ["BEAM-1", "BEAM-2"]
    assert all(beam.TreatmentMachineName == "LINAC-A" for beam in saved["RTPLAN"].BeamSequence)
    assert saved["RTPLAN"].ReferencedImageSequence[0].ReferencedSOPInstanceUID == saved["CT"].SOPInstanceUID
    assert validate_directory(destination).ok


@pytest.mark.parametrize("tamper", [
    "replace_equipment", "delete_equipment", "introduce_equipment", "restore_identifier",
    "replace_nested_equipment", "replace_beam_name", "delete_beam_name",
])
def test_independent_validation_rejects_equipment_and_beam_text_tampering(
    tmp_path, dicom_factory, dicom_writer, tamper
):
    source, destination = tmp_path / "source", tmp_path / "output"
    plan = dicom_factory("RTPLAN", patient_id="CASE-24018")
    plan.Manufacturer = "Acme Medical"
    plan.ManufacturerModelName = "Model CASE-24018"
    plan.BeamSequence = [_beam(1, "Original identity label")]
    dicom_writer(plan, source / "plan.dcm")
    scan = scan_directory(source)
    report = process_batch(source, destination)
    assert report.ok, report.to_dict()
    path = next(destination.rglob("*.dcm"))
    saved = pydicom.dcmread(path)
    after = capture_snapshot(saved)
    mapping = {}
    for tag_path, originals in scan.files[0].snapshot.entity_uids.items():
        mapping.update(zip(originals, after.entity_uids[tag_path], strict=True))
    outputs = [ExpectedOutput(scan.files[0], path, str(saved.PatientID), 0)]
    baseline = validate_batch(scan, outputs, mapping, options=Options())
    assert not any(f.severity == "FAIL" for f in baseline), baseline
    if tamper == "replace_equipment":
        saved.Manufacturer = "Different manufacturer"
    elif tamper == "delete_equipment":
        del saved.Manufacturer
    elif tamper == "introduce_equipment":
        saved.MaterialID = "NEW-MATERIAL"
    elif tamper == "restore_identifier":
        saved.ManufacturerModelName = plan.ManufacturerModelName
    elif tamper == "replace_nested_equipment":
        saved.BeamSequence[0].TreatmentMachineName = "LINAC-B"
    elif tamper == "replace_beam_name":
        saved.BeamSequence[0].BeamName = "Original identity label"
    else:
        del saved.BeamSequence[0].BeamName
    dicom_writer(saved, path)
    findings = validate_batch(scan, outputs, mapping, options=Options())
    assert any(
        f.severity == "FAIL" and f.code == "EQUIPMENT_TEXT_CHANGED" for f in findings
    ), findings
    assert not any(
        f.severity == "PASS" and f.code in {"CLINICAL_PRESERVATION", "METADATA"}
        for f in findings
    )
    serialized = json.dumps([finding.__dict__ for finding in findings])
    for value in ("CASE-24018", "Original identity label", "Different manufacturer", "LINAC-B"):
        assert value not in serialized

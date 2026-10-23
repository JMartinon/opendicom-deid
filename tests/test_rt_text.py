"""Synthetic RT labels and prescriptions survive without known identifying text."""

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
from opendicom_deid.core.policy import scrub_metadata
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.validator import validate_batch, validate_directory


def _scrub(dataset, **kwargs):
    originals = dict.fromkeys(
        uid for values in capture_snapshot(dataset).entity_uids.values() for uid in values
    )
    mapping = {uid: f"2.25.{index}" for index, uid in enumerate(originals, 1)}
    scrub_metadata(dataset, pseudonym="SUBJECT-TEST", uid_mapping=mapping, **kwargs)


def _beam(number, name):
    beam = Dataset()
    beam.BeamNumber = number
    beam.BeamName = name
    beam.BeamDescription = "6 MV photon field"
    beam.TreatmentMachineName = "LINAC-A"
    beam.Manufacturer = "Acme Devices"
    beam.ManufacturerModelName = "Linac Pro"
    beam.BeamType = "STATIC"
    beam.RadiationType = "PHOTON"
    beam.NumberOfControlPoints = 2
    beam.FinalCumulativeMetersetWeight = 1
    beam.ControlPointSequence = []
    for index in range(2):
        point = Dataset()
        point.ControlPointIndex = index
        point.CumulativeMetersetWeight = index
        point.GantryAngle = number * 45
        point.NominalBeamEnergy = 6
        point.IsocenterPosition = [1, 2, 3]
        device = Dataset()
        device.RTBeamLimitingDeviceType = "MLCX"
        device.LeafJawPositions = [-10, -8, 10, 8]
        point.BeamLimitingDevicePositionSequence = [device]
        beam.ControlPointSequence.append(point)
    return beam


def _clinical_chain(dicom_factory):
    ct = dicom_factory(patient_id="CASE-68492", patient_name="TESTPERSON^ALICIA")
    common = {
        "patient_id": str(ct.PatientID), "patient_name": str(ct.PatientName),
        "study": str(ct.StudyInstanceUID), "frame": str(ct.FrameOfReferenceUID),
    }
    struct = dicom_factory("RTSTRUCT", **common)
    plan = dicom_factory("RTPLAN", **common)
    dose = dicom_factory("RTDOSE", **common)
    for ds in (ct, struct, plan, dose):
        ds.ReferringPhysicianName = "DRTEST^ORION"
        ds.InstitutionName = "Fictionville Medical Center"
        ds.InstitutionAddress = "725 Mock Avenue"
        ds.OperatorsName = "OPERATORTEST^VEGA"
        ds.add_new(0x00110010, "LO", "Synthetic private creator")
        ds.add_new(0x00111001, "LO", "CASE-68492")
    struct.StructureSetLabel = "PELVIS-SET"
    struct.StructureSetName = "Pelvis structures"
    struct.StructureSetDescription = "Targets and organs at risk"
    struct.ReferencedImageSequence = [reference(ct)]
    struct.StructureSetROISequence = []
    struct.ROIContourSequence = []
    for number, name in enumerate(("PTV_70Gy", "Rectum", "Bladder"), 1):
        roi = Dataset()
        roi.ROINumber = number
        roi.ROIName = name
        roi.ROIDescription = "Target volume" if number == 1 else "Organ at risk"
        roi.ReferencedFrameOfReferenceUID = ct.FrameOfReferenceUID
        roi.ROIGenerationAlgorithm = "MANUAL"
        struct.StructureSetROISequence.append(roi)
        contour_roi = Dataset()
        contour_roi.ReferencedROINumber = number
        contour_roi.ROIDisplayColor = [number * 50, 100, 150]
        contour = Dataset()
        contour.ContourGeometricType = "CLOSED_PLANAR"
        contour.NumberOfContourPoints = 3
        contour.ContourData = [0, 0, 25, 1, 0, 25, 0, 1, 25]
        contour.ContourImageSequence = [reference(ct)]
        contour_roi.ContourSequence = [contour]
        struct.ROIContourSequence.append(contour_roi)
    plan.RTPlanLabel = "PELVIS-70"
    plan.RTPlanName = "Pelvis 70 Gy"
    plan.RTPlanDescription = "Definitive pelvic treatment"
    plan.TreatmentProtocols = ["PROSTATE-70", "IMRT"]
    plan.TreatmentSites = "PELVIS"
    plan.PrescriptionDescription = "PTV 70 Gy in 35 fractions; rectum V50 < 50%"
    plan.BeamSequence = [_beam(1, "Anterior"), _beam(2, "Posterior")]
    plan.ReferencedStructureSetSequence = [reference(struct)]
    plan.ReferencedDoseSequence = [reference(dose)]
    fraction = Dataset()
    fraction.FractionGroupNumber = 1
    fraction.FractionGroupDescription = "35 daily fractions"
    fraction.NumberOfFractionsPlanned = 35
    fraction.NumberOfBeams = 2
    fraction.ReferencedBeamSequence = []
    for number, meterset in ((1, 147.5), (2, 162.25)):
        ref = Dataset()
        ref.ReferencedBeamNumber = number
        ref.BeamDose = 1
        ref.BeamMeterset = meterset
        fraction.ReferencedBeamSequence.append(ref)
    plan.FractionGroupSequence = [fraction]
    target, organ = Dataset(), Dataset()
    target.DoseReferenceNumber = 1
    target.DoseReferenceStructureType = "VOLUME"
    target.DoseReferenceType = "TARGET"
    target.DoseReferenceDescription = "PTV_70Gy prescription"
    target.TargetPrescriptionDose = 70
    target.TargetMinimumDose = 66.5
    target.TargetMaximumDose = 74.9
    target.TargetUnderdoseVolumeFraction = 0.05
    organ.DoseReferenceNumber = 2
    organ.DoseReferenceStructureType = "VOLUME"
    organ.DoseReferenceType = "ORGAN_AT_RISK"
    organ.DoseReferenceDescription = "Rectum constraints"
    organ.OrganAtRiskFullVolumeDose = 30
    organ.OrganAtRiskLimitDose = 50
    organ.OrganAtRiskMaximumDose = 70
    organ.OrganAtRiskOverdoseVolumeFraction = 0.5
    plan.DoseReferenceSequence = [target, organ]
    objective = Dataset()
    objective.DosimetricObjectiveWeight = 1.5
    plan.DosimetricObjectiveSequence = [objective]
    dose.ReferencedRTPlanSequence = [reference(plan)]
    return {str(ds.Modality): ds for ds in (ct, struct, plan, dose)}


def _write_and_process(tmp_path, dicom_writer, originals, *, text_policy="clinical"):
    source, destination = tmp_path / "source", tmp_path / "output"
    for index, ds in enumerate(originals.values(), 1):
        dicom_writer(ds, source / f"{index:03}.dcm")
    hashes = {p: hashlib.sha256(p.read_bytes()).digest() for p in source.iterdir()}
    scan = scan_directory(source)
    report = process_batch(source, destination, Options(text_policy=text_policy))
    assert report.ok, report.to_dict()
    saved = {
        str(ds.Modality): (path, ds)
        for path in destination.rglob("*.dcm") for ds in [pydicom.dcmread(path)]
    }
    assert {p: hashlib.sha256(p.read_bytes()).digest() for p in source.iterdir()} == hashes
    return scan, report, saved, destination


@pytest.mark.parametrize("keyword,value", [
    ("ROIName", "PTV_70Gy"), ("ROIDescription", "Primary target volume"),
    ("StructureSetLabel", "PELVIS-SET"), ("StructureSetName", "Pelvis structures"),
    ("StructureSetDescription", "Targets and organs at risk"),
    ("ROIObservationLabel", "PTV70"),
    ("RTPlanLabel", "PELVIS-70"), ("RTPlanName", "Pelvis 70 Gy"),
    ("RTPlanDescription", "Definitive pelvic treatment"),
    ("PrescriptionDescription", "PTV 70 Gy in 35 fractions"),
    ("DoseReferenceDescription", "Rectum maximum dose"),
    ("FractionGroupDescription", "35 daily fractions"),
    ("TreatmentProtocols", ["PROSTATE-70", "IMRT"]), ("TreatmentSites", "PELVIS"),
    ("ApplicatorID", "APPLICATOR-A"), ("AccessoryCode", "ACCESSORY-B"),
    ("WedgeID", "WEDGE-C"), ("GantryID", "GANTRY-A"),
    ("PatientSupportID", "COUCH-A"),
])
def test_direct_scrubbing_preserves_allowlisted_clinical_text_by_default(
    dicom_factory, keyword, value
):
    plan = dicom_factory("RTPLAN")
    setattr(plan, keyword, deepcopy(value))
    _scrub(plan)
    assert getattr(plan, keyword) == value


def test_default_pipeline_preserves_rt_names_prescription_constraints_and_linked_geometry(
    tmp_path, dicom_factory, dicom_writer
):
    originals = _clinical_chain(dicom_factory)
    _, report, saved, destination = _write_and_process(tmp_path, dicom_writer, originals)
    assert report.output_count == 4
    assert [roi.ROIName for roi in saved["RTSTRUCT"][1].StructureSetROISequence] == [
        "PTV_70Gy", "Rectum", "Bladder"
    ]
    assert [beam.BeamName for beam in saved["RTPLAN"][1].BeamSequence] == ["Anterior", "Posterior"]
    assert saved["RTPLAN"][1].PrescriptionDescription == originals["RTPLAN"].PrescriptionDescription
    assert saved["RTPLAN"][1].DoseReferenceSequence == originals["RTPLAN"].DoseReferenceSequence
    assert saved["RTPLAN"][1].FractionGroupSequence == originals["RTPLAN"].FractionGroupSequence
    for modality, (_, ds) in saved.items():
        original = originals[modality]
        assert capture_snapshot(ds).clinical == capture_snapshot(original).clinical
        assert ds.PatientID == ds.PatientName != original.PatientID
        assert ds.PatientBirthDate == ""
        for name in ("ReferringPhysicianName", "OperatorsName", "InstitutionName", "InstitutionAddress"):
            assert str(ds.get(name, "")) in {"", "ANONYMIZED"}
        assert all(not element.tag.is_private for element in ds.iterall())
        assert ds.SOPInstanceUID != original.SOPInstanceUID
        if "PixelData" in original:
            assert ds.PixelData == original.PixelData
    structure, plan, dose = [saved[modality][1] for modality in ("RTSTRUCT", "RTPLAN", "RTDOSE")]
    assert len(structure.ROIContourSequence) == 3
    assert [len(roi.ContourSequence) for roi in structure.ROIContourSequence] == [1, 1, 1]
    assert plan.ReferencedStructureSetSequence[0].ReferencedSOPInstanceUID == structure.SOPInstanceUID
    assert plan.ReferencedDoseSequence[0].ReferencedSOPInstanceUID == dose.SOPInstanceUID
    assert dose.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID == plan.SOPInstanceUID
    for roi in structure.ROIContourSequence:
        assert roi.ContourSequence[0].ContourImageSequence[0].ReferencedSOPInstanceUID == (
            saved["CT"][1].SOPInstanceUID
        )
    assert validate_directory(destination).ok


@pytest.mark.parametrize("field,identifier,label", [
    ("PatientName", "TESTPERSON^ALICIA", "PTV testperson 70Gy"),
    ("PatientName", "García^Lucía", "PTV GARCIA 70Gy"),
    ("PatientName", "TESTPERSON^ALICIA", "PTV alicia 70Gy"),
    ("PatientID", "CASE-68492", "PTV case 68492 70Gy"),
    ("ReferringPhysicianName", "DRTEST^ORION", "PTV DRTEST 70Gy"),
    ("OperatorsName", "OPERATORTEST^VEGA", "PTV VEGA 70Gy"),
    ("InstitutionName", "Fictionville Medical Center", "PTV Fictionville 70Gy"),
    ("InstitutionAddress", "725 Mock Avenue", "PTV 725 Mock Avenue 70Gy"),
    ("PatientBirthDate", "19760203", "PTV 1976-02-03 70Gy"),
])
def test_known_identifier_spans_removed_without_losing_clinical_prefix_or_suffix(
    dicom_factory, field, identifier, label
):
    plan = dicom_factory("RTPLAN")
    setattr(plan, field, identifier)
    plan.DoseReferenceSequence = [Dataset()]
    plan.DoseReferenceSequence[0].DoseReferenceDescription = label
    _scrub(plan)
    result = plan.DoseReferenceSequence[0].DoseReferenceDescription
    assert " ".join(result.split()) == "PTV 70Gy"


def test_entire_identifying_clinical_label_becomes_neutral(dicom_factory):
    plan = dicom_factory("RTPLAN", patient_name="TESTPERSON^ALICIA")
    plan.RTPlanName = "TESTPERSON"
    _scrub(plan)
    assert plan.RTPlanName == "ANONYMIZED"


def test_nested_identity_fields_are_removed_and_equipment_keeps_whole_value_redaction(
    dicom_factory,
):
    plan = dicom_factory("RTPLAN")
    beam = _beam(1, "PTV NESTED 70Gy")
    beam.PatientName = "NESTED^ALICIA"
    beam.PatientID = "NESTED-68492"
    beam.PatientBirthDate = "19760203"
    beam.ReferringPhysicianName = "DRORION^VEGA"
    beam.InstitutionName = "Fictionville Medical Center"
    beam.TreatmentMachineName = "Fictionville X"
    beam.BeamDescription = "PTV DRORION 70Gy"
    beam.add_new(0x00110010, "LO", "Synthetic private creator")
    beam.add_new(0x00111001, "LO", "NESTED-68492")
    plan.BeamSequence = [beam]
    _scrub(plan)
    saved = plan.BeamSequence[0]
    assert " ".join(saved.BeamName.split()) == "PTV 70Gy"
    assert " ".join(saved.BeamDescription.split()) == "PTV 70Gy"
    assert saved.TreatmentMachineName == "ANONYMIZED"
    assert saved.PatientName == saved.PatientID == "SUBJECT-TEST"
    assert saved.PatientBirthDate == ""
    for keyword in ("ReferringPhysicianName", "InstitutionName"):
        assert str(saved.get(keyword, "")) in {"", "ANONYMIZED"}
    assert all(not element.tag.is_private for element in saved.iterall())


def test_batch_screens_later_patient_physician_and_hospital_identifiers(
    tmp_path, dicom_factory, dicom_writer
):
    plan = dicom_factory("RTPLAN", patient_id="CASE-68492", patient_name="TESTPERSON^ALICIA")
    plan.PrescriptionDescription = "PTV LATERSUBJECT 70Gy; reviewed DOCTORLATER at FICTIONVILLE"
    plan.BeamSequence = [_beam(1, "LATERSUBJECT AP")]
    later = dicom_factory(patient_id="LATER-93428", patient_name="LATERSUBJECT^BOB")
    later.ReferringPhysicianName = "DOCTORLATER^VEGA"
    later.InstitutionName = "Fictionville Medical Center"
    _, report, saved, _ = _write_and_process(
        tmp_path, dicom_writer, {"RTPLAN": plan, "CT": later}
    )
    prescription = saved["RTPLAN"][1].PrescriptionDescription
    for identity in ("LATERSUBJECT", "DOCTORLATER", "FICTIONVILLE"):
        assert identity not in prescription.upper()
        assert identity not in json.dumps(report.to_dict()).upper()
    assert "PTV" in prescription and "70Gy" in prescription
    assert saved["RTPLAN"][1].BeamSequence[0].BeamName.strip() == "AP"


def test_strict_policy_scrubs_rt_labels_but_keeps_constraints_and_machine_values(
    tmp_path, dicom_factory, dicom_writer
):
    originals = _clinical_chain(dicom_factory)
    _, _, saved, destination = _write_and_process(
        tmp_path, dicom_writer, originals, text_policy="strict"
    )
    plan, struct = saved["RTPLAN"][1], saved["RTSTRUCT"][1]
    assert [beam.BeamName for beam in plan.BeamSequence] == ["BEAM-1", "BEAM-2"]
    assert all(roi.ROIName == "ANONYMIZED" for roi in struct.StructureSetROISequence)
    assert str(plan.get("PrescriptionDescription", "")) in {"", "ANONYMIZED"}
    assert all(beam.TreatmentMachineName == "LINAC-A" for beam in plan.BeamSequence)
    assert plan.DoseReferenceSequence[0].TargetPrescriptionDose == 70
    assert plan.DoseReferenceSequence[1].OrganAtRiskMaximumDose == 70
    assert capture_snapshot(plan).clinical == capture_snapshot(originals["RTPLAN"]).clinical
    assert validate_directory(destination).ok


def _expected_outputs(scan, saved):
    outputs, mapping = [], {}
    for record in scan.files:
        path, ds = saved[record.modality]
        after = capture_snapshot(ds)
        for tag_path, originals in record.snapshot.entity_uids.items():
            mapping.update(zip(originals, after.entity_uids[tag_path], strict=True))
        outputs.append(ExpectedOutput(record, path, str(ds.PatientID), 0))
    return outputs, mapping


@pytest.mark.parametrize("tamper", ["roi", "beam", "prescription", "delete", "introduce"])
def test_source_comparison_rejects_rt_text_changes_without_exposing_values(
    tmp_path, dicom_factory, dicom_writer, tamper
):
    scan, _, saved, _ = _write_and_process(tmp_path, dicom_writer, _clinical_chain(dicom_factory))
    expected, mapping = _expected_outputs(scan, saved)
    baseline = validate_batch(scan, expected, mapping, options=Options())
    assert not any(f.severity == "FAIL" for f in baseline), baseline
    path, ds = saved["RTSTRUCT" if tamper == "roi" else "RTPLAN"]
    if tamper == "roi":
        ds.StructureSetROISequence[0].ROIName = "MUTATED-TEXT"
    elif tamper == "beam":
        ds.BeamSequence[0].BeamName = "MUTATED-TEXT"
    elif tamper == "prescription":
        ds.PrescriptionDescription = "MUTATED-TEXT"
    elif tamper == "delete":
        del ds.DoseReferenceSequence[0].DoseReferenceDescription
    else:
        ds.BeamSequence[0].AccessoryCode = "MUTATED-TEXT"
    dicom_writer(ds, path)
    findings = validate_batch(scan, expected, mapping, options=Options())
    assert any(f.code == "RT_TEXT_CHANGED" and f.severity == "FAIL" for f in findings), findings
    assert not any(f.code == "CLINICAL_PRESERVATION" and f.severity == "PASS" for f in findings)
    assert "MUTATED-TEXT" not in json.dumps([finding.__dict__ for finding in findings])


@pytest.mark.parametrize("tamper", [
    "prescription_number", "reference_type", "objective_weight", "empty_sequence_item",
])
def test_rt_prescription_numbers_and_sequence_cardinality_are_validated(
    tmp_path, dicom_factory, dicom_writer, tamper
):
    scan, _, saved, _ = _write_and_process(tmp_path, dicom_writer, _clinical_chain(dicom_factory))
    expected, mapping = _expected_outputs(scan, saved)
    path, plan = saved["RTPLAN"]
    if tamper == "prescription_number":
        plan.DoseReferenceSequence[0].TargetPrescriptionDose = 75
    elif tamper == "reference_type":
        plan.DoseReferenceSequence[0].DoseReferenceType = "ORGAN_AT_RISK"
    elif tamper == "objective_weight":
        plan.DosimetricObjectiveSequence[0].DosimetricObjectiveWeight = 2.5
    else:
        plan.DosimetricObjectiveSequence.append(Dataset())
    dicom_writer(plan, path)
    findings = validate_batch(scan, expected, mapping, options=Options())
    assert any(f.code == "CLINICAL_DATA_CHANGED" and f.severity == "FAIL" for f in findings), findings
    assert not any(f.code == "CLINICAL_PRESERVATION" and f.severity == "PASS" for f in findings)


def test_standalone_validation_uses_the_saved_text_policy_marker(
    tmp_path, dicom_factory, dicom_writer
):
    plan = dicom_factory("RTPLAN")
    plan.RTPlanName = "Pelvis 70 Gy"
    clinical, strict = deepcopy(plan), deepcopy(plan)
    _scrub(clinical)
    _scrub(strict, text_policy="strict")
    assert clinical.DeidentificationMethod != strict.DeidentificationMethod
    clinical_path, strict_path = tmp_path / "clinical" / "plan.dcm", tmp_path / "strict" / "plan.dcm"
    dicom_writer(clinical, clinical_path)
    dicom_writer(strict, strict_path)
    assert validate_directory(clinical_path.parent).ok
    assert validate_directory(strict_path.parent).ok
    strict.RTPlanName = "Pelvis 70 Gy"
    dicom_writer(strict, strict_path)
    assert not validate_directory(strict_path.parent).ok


_LEGACY_MARKER = [
    "OpenDICOM De-ID metadata policy v2; no profile conformance claim",
    "Pixel data unchanged; privacy review required",
]


def test_legacy_strict_output_passes_standalone_but_not_current_batch_validation(
    tmp_path, dicom_factory, dicom_writer
):
    scan, _, saved, destination = _write_and_process(
        tmp_path, dicom_writer, _clinical_chain(dicom_factory), text_policy="strict"
    )
    expected, mapping = _expected_outputs(scan, saved)
    for path, ds in saved.values():
        ds.DeidentificationMethod = _LEGACY_MARKER
        dicom_writer(ds, path)
    standalone = validate_directory(destination)
    assert standalone.ok, standalone.to_dict()
    findings = validate_batch(scan, expected, mapping, options=Options(text_policy="strict"))
    assert any(f.severity == "FAIL" and f.code == "RESIDUAL_METADATA" for f in findings), findings
    assert not any(f.severity == "PASS" and f.code == "METADATA" for f in findings)


def test_legacy_marker_cannot_allow_retained_clinical_names(
    tmp_path, dicom_factory, dicom_writer
):
    plan = dicom_factory("RTPLAN")
    plan.RTPlanName = "Pelvis 70 Gy"
    _scrub(plan)
    plan.DeidentificationMethod = _LEGACY_MARKER
    dicom_writer(plan, tmp_path / "output" / "plan.dcm")
    report = validate_directory(tmp_path / "output")
    assert not report.ok
    assert any(f.code == "RESIDUAL_METADATA" and f.severity == "FAIL" for f in report.findings)


@pytest.mark.parametrize("marker", [
    None,
    "",
    _LEGACY_MARKER + ["Clinical RT text retained"],
    _LEGACY_MARKER + ["Clinical RT text scrubbed"],
    ["Unknown policy", "Pixel data unchanged; privacy review required"],
])
def test_empty_mixed_or_unknown_policy_markers_fail_standalone_validation(
    tmp_path, dicom_factory, dicom_writer, marker
):
    plan = dicom_factory("RTPLAN")
    _scrub(plan, text_policy="strict")
    plan.DeidentificationMethod = marker
    dicom_writer(plan, tmp_path / "output" / "plan.dcm")
    assert not validate_directory(tmp_path / "output").ok

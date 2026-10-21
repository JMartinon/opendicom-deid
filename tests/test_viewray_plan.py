"""Synthetic public RT beam content for the one observed ViewRay storage class.

These fixtures exercise guarded de-identification, not vendor conformance or
treatment-machine compatibility. They never contain patient exports.
"""

from __future__ import annotations

import copy
import json
import struct

import pydicom
import pytest
from conftest import make_dataset, reference, write_dataset
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, generate_uid

from opendicom_deid.cli import main
from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.inspection import capture_snapshot, walk_elements
from opendicom_deid.core.models import ExpectedOutput, Options
from opendicom_deid.core.reader import read_dicom
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.sop_classes import VIEWRAY_RT_PLAN_STORAGE
from opendicom_deid.core.storage_inspection import inspect_storage
from opendicom_deid.core.validator import validate_batch, validate_directory


def _public_plan_options():
    return Options(allow_viewray_public_plan=True)


def _viewray_plan(**kwargs):
    plan = make_dataset("RTPLAN", **kwargs)
    plan.SOPClassUID = VIEWRAY_RT_PLAN_STORAGE
    plan.file_meta.MediaStorageSOPClassUID = VIEWRAY_RT_PLAN_STORAGE
    plan.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    beam = Dataset()
    beam.BeamNumber = 1
    beam.BeamType = "STATIC"
    beam.RadiationType = "PHOTON"
    beam.NumberOfControlPoints = 2
    beam.FinalCumulativeMetersetWeight = 1
    points = []
    for index in range(2):
        point = Dataset()
        point.ControlPointIndex = index
        point.CumulativeMetersetWeight = index
        point.GantryAngle = 90
        point.NominalBeamEnergy = 6
        point.IsocenterPosition = [12.5, 20, -3.2]
        device = Dataset()
        device.RTBeamLimitingDeviceType = "MLCX"
        device.LeafJawPositions = [-10, -8, 10, 8]
        point.BeamLimitingDevicePositionSequence = [device]
        points.append(point)
    beam.ControlPointSequence = points
    plan.BeamSequence = [beam]
    referenced_beam = Dataset()
    referenced_beam.ReferencedBeamNumber = 1
    plan.ReferencedBeamSequence = [referenced_beam]
    return plan


def test_viewray_plan_in_linked_implicit_vr_rt_batch_preserves_public_content(tmp_path):
    study, frame = generate_uid(), generate_uid()
    datasets = {
        "ct": make_dataset("CT", study=study, frame=frame),
        "mr": make_dataset("MR", study=study, frame=frame),
        "structure": make_dataset("RTSTRUCT", study=study, frame=frame),
        "plan": _viewray_plan(study=study, frame=frame),
        "dose": make_dataset("RTDOSE", study=study, frame=frame),
        "image": make_dataset("RTIMAGE", study=study, frame=frame),
    }
    ct, mr, structure, plan, dose, image = datasets.values()
    frame_item = Dataset()
    frame_item.FrameOfReferenceUID = frame
    study_item = Dataset()
    series_items = []
    for source_image in (ct, mr):
        series_item = Dataset()
        series_item.SeriesInstanceUID = source_image.SeriesInstanceUID
        series_item.ContourImageSequence = [reference(source_image)]
        series_items.append(series_item)
    study_item.RTReferencedSeriesSequence = series_items
    frame_item.RTReferencedStudySequence = [study_item]
    structure.ReferencedFrameOfReferenceSequence = [frame_item]
    contour = Dataset()
    contour.ContourGeometricType = "CLOSED_PLANAR"
    contour.NumberOfContourPoints = 3
    contour.ContourData = [0, 0, 0, 10, 0, 0, 0, 10, 0]
    contour.ContourImageSequence = [reference(ct)]
    roi = Dataset()
    roi.ReferencedROINumber = 1
    roi.ContourSequence = [contour]
    structure.ROIContourSequence = [roi]
    plan.ReferencedStructureSetSequence = [reference(structure)]
    plan.ReferencedDoseSequence = [reference(dose)]
    dose.ReferencedRTPlanSequence = [reference(plan)]
    image.ReferencedRTPlanSequence = [reference(plan)]
    plan.add_new(0x01F10010, "LO", "SYNTHETIC PRIVATE CREATOR")
    plan.add_new(0x01F11001, "LO", "SYNTHETIC PRIVATE IDENTITY")
    plan.BeamSequence[0].ControlPointSequence[0].add_new(
        0x01F11001, "LO", "SYNTHETIC NESTED IDENTITY"
    )
    source, destination = tmp_path / "source", tmp_path / "output"
    originals = {}
    for index, (name, dataset) in enumerate(datasets.items(), 1):
        dataset.InstanceNumber = index
        dataset.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
        path = write_dataset(dataset, source / name / "synthetic.dcm")
        originals[index] = capture_snapshot(read_dicom(path))

    scan = scan_directory(source, allow_viewray_public_plan=True)
    assert scan.ok, scan.findings
    assert scan.modalities == {
        "CT": 1, "MR": 1, "RTSTRUCT": 1, "RTPLAN": 1, "RTDOSE": 1, "RTIMAGE": 1,
    }
    assert any(f.code == "VIEWRAY_PRIVATE_PLAN" and f.severity == "WARNING" for f in scan.findings)
    report = process_batch(source, destination, options=_public_plan_options())
    assert report.ok, report.to_dict()
    assert report.output_count == 6
    saved = {
        int(dataset.InstanceNumber): dataset
        for dataset in (pydicom.dcmread(path) for path in destination.rglob("*.dcm"))
    }
    mapping = {
        originals[index].sop_uid: str(dataset.SOPInstanceUID)
        for index, dataset in saved.items()
    }
    assert not mapping.keys() & set(mapping.values())
    assert len({str(dataset.PatientID) for dataset in saved.values()}) == 1
    assert len({str(dataset.FrameOfReferenceUID) for dataset in saved.values()}) == 1
    for index, dataset in saved.items():
        before, after = originals[index], capture_snapshot(dataset)
        assert after.sop_class_uid == before.sop_class_uid
        assert after.technical_uids == before.technical_uids
        assert after.clinical == before.clinical
        assert after.transfer_syntax_uid == str(ImplicitVRLittleEndian)
        assert after.references == {
            path: tuple(mapping[uid] for uid in values)
            for path, values in before.references.items()
        }
        assert dataset.PatientIdentityRemoved == "YES"
        assert dataset.PatientName != "Synthetic^Person"
        assert all(not element.tag.is_private for _, element in walk_elements(dataset))
    assert sum(len(uids) for snap in originals.values() for uids in snap.references.values()) == 7
    assert saved[4].SOPClassUID == VIEWRAY_RT_PLAN_STORAGE
    assert saved[4].file_meta.MediaStorageSOPClassUID == VIEWRAY_RT_PLAN_STORAGE
    for index in (5, 6):
        assert saved[index].ReferencedRTPlanSequence[0].ReferencedSOPClassUID == VIEWRAY_RT_PLAN_STORAGE
    for index, original in ((1, ct), (2, mr), (5, dose), (6, image)):
        assert saved[index].PixelData == original.PixelData
    saved_points = saved[4].BeamSequence[0].ControlPointSequence
    assert [float(point.CumulativeMetersetWeight) for point in saved_points] == [0, 1]
    assert saved_points[0].BeamLimitingDevicePositionSequence[0].LeafJawPositions == [-10, -8, 10, 8]
    standalone = validate_directory(destination)
    assert standalone.ok, standalone.to_dict()
    assert any(
        f.code == "VIEWRAY_PRIVATE_PLAN" and f.severity == "WARNING"
        for f in standalone.findings
    )


def test_viewray_malformed_private_fd_removed_at_root_and_nested_control_point(tmp_path):
    plan = _viewray_plan()
    plan.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    point = plan.BeamSequence[0].ControlPointSequence[0]
    point.add_new(0x01F10010, "LO", "VIEWRAY")
    point.add_new(0x01F11026, "FD", 0.938)
    # Undefined lengths let this fixture shorten the private payload without
    # damaging the surrounding public sequence boundaries.
    for _, element in walk_elements(plan):
        if element.VR == "SQ":
            element.is_undefined_length = True
            for item in element.value:
                item.is_undefined_length_sequence_item = True
    source, destination = tmp_path / "source", tmp_path / "output"
    path = write_dataset(plan, source / "synthetic.dcm")
    valid = struct.pack("<HH2sHd", 0x01F1, 0x1026, b"FD", 8, 0.938)
    malformed = struct.pack("<HH2sH", 0x01F1, 0x1026, b"FD", 6) + b"0.938 "
    data = path.read_bytes()
    assert data.count(valid) == 1
    path.write_bytes(data.replace(valid, malformed) + malformed)
    previous = pydicom.config.convert_wrong_length_to_UN
    pydicom.config.convert_wrong_length_to_UN = False
    try:
        raw = pydicom.dcmread(path)
        with pytest.raises(pydicom.errors.BytesLengthException):
            _ = raw[0x01F11026].value
        with pytest.raises(pydicom.errors.BytesLengthException):
            _ = raw.BeamSequence[0].ControlPointSequence[0][0x01F11026].value
        report = process_batch(source, destination, options=_public_plan_options())
        assert report.ok, report.to_dict()
        saved = read_dicom(next(destination.rglob("*.dcm")), remove_private=False)
        assert all(not element.tag.is_private for _, element in walk_elements(saved))
        assert pydicom.config.convert_wrong_length_to_UN is False
    finally:
        pydicom.config.convert_wrong_length_to_UN = previous


@pytest.mark.parametrize("invalid", [
    "no_beams", "empty_beams", "private_only", "zero_beam_number", "duplicate_beam_number",
    "missing_points", "empty_points", "one_point", "count_mismatch", "index_mismatch",
    "missing_weight", "nonzero_initial_weight", "negative_weight", "decreasing_weight",
    "zero_final_weight", "final_weight_mismatch", "no_public_geometry", "short_isocenter",
    "empty_mlc", "invalid_later_geometry", "pixels",
])
def test_viewray_plan_rejects_missing_or_inconsistent_public_beam_content(tmp_path, invalid):
    plan = _viewray_plan()
    beam = plan.BeamSequence[0]
    points = beam.ControlPointSequence
    if invalid == "no_beams":
        del plan.BeamSequence
    elif invalid == "empty_beams":
        plan.BeamSequence = []
    elif invalid == "private_only":
        del plan.BeamSequence
        plan.add_new(0x01F11001, "OB", b"private plan payload")
    elif invalid == "zero_beam_number":
        beam.BeamNumber = 0
    elif invalid == "duplicate_beam_number":
        plan.BeamSequence.append(copy.deepcopy(beam))
    elif invalid == "missing_points":
        del beam.ControlPointSequence
    elif invalid == "empty_points":
        beam.ControlPointSequence = []
    elif invalid == "one_point":
        beam.NumberOfControlPoints = 1
        beam.ControlPointSequence = [points[0]]
    elif invalid == "count_mismatch":
        beam.NumberOfControlPoints = 3
    elif invalid == "index_mismatch":
        points[1].ControlPointIndex = 2
    elif invalid == "missing_weight":
        del points[1].CumulativeMetersetWeight
    elif invalid == "nonzero_initial_weight":
        points[0].CumulativeMetersetWeight = 0.1
    elif invalid == "negative_weight":
        points[1].CumulativeMetersetWeight = -1
    elif invalid == "decreasing_weight":
        middle = copy.deepcopy(points[1])
        middle.CumulativeMetersetWeight = 2
        points[1].ControlPointIndex = 2
        points.insert(1, middle)
        beam.NumberOfControlPoints = 3
    elif invalid == "zero_final_weight":
        points[1].CumulativeMetersetWeight = 0
        beam.FinalCumulativeMetersetWeight = 0
    elif invalid == "final_weight_mismatch":
        beam.FinalCumulativeMetersetWeight = 2
    elif invalid == "no_public_geometry":
        for point in points:
            for keyword in (
                "GantryAngle", "NominalBeamEnergy", "IsocenterPosition",
                "BeamLimitingDevicePositionSequence",
            ):
                delattr(point, keyword)
    elif invalid == "short_isocenter":
        points[0].IsocenterPosition = [12.5, 20]
    elif invalid == "empty_mlc":
        points[0].BeamLimitingDevicePositionSequence[0].LeafJawPositions = []
    elif invalid == "invalid_later_geometry":
        points[1].IsocenterPosition = [12.5, 20]
    elif invalid == "pixels":
        plan.PixelData = b"\0\0"
    source, destination = tmp_path / "source", tmp_path / "output"
    write_dataset(plan, source / "synthetic.dcm")
    report = process_batch(source, destination, options=_public_plan_options())
    assert not report.ok, report.to_dict()
    assert report.output_count == 0
    assert any(f.code == "INVALID_STRUCTURE" and f.severity == "FAIL" for f in report.findings)
    assert not destination.exists()


@pytest.mark.parametrize("invalid,code", [
    ("adjacent_vendor_uid", "UNSUPPORTED_OBJECT"),
    ("wrong_modality", "MODALITY"),
    ("meta_class_mismatch", "INPUT_METADATA"),
])
def test_viewray_plan_support_is_limited_to_exact_consistent_class(tmp_path, invalid, code):
    plan = _viewray_plan()
    if invalid == "adjacent_vendor_uid":
        plan.SOPClassUID = VIEWRAY_RT_PLAN_STORAGE[:-1] + "6"
        plan.file_meta.MediaStorageSOPClassUID = plan.SOPClassUID
    elif invalid == "wrong_modality":
        plan.Modality = "CT"
    source, destination = tmp_path / "source", tmp_path / "output"
    path = write_dataset(plan, source / "synthetic.dcm")
    if invalid == "meta_class_mismatch":
        # dcmwrite repairs class mismatch, so alter only file-meta bytes afterward.
        uid = VIEWRAY_RT_PLAN_STORAGE.encode()
        assert path.read_bytes().count(uid) == 2
        path.write_bytes(path.read_bytes().replace(uid, uid[:-1] + b"6", 1))
    report = process_batch(source, destination, options=_public_plan_options())
    assert not report.ok, report.to_dict()
    assert report.output_count == 0
    assert any(f.code == code and f.severity == "FAIL" for f in report.findings)
    assert not destination.exists()


@pytest.mark.parametrize("tamper", [
    "gantry", "mlc", "weight", "referenced_beam", "sequence_300a", "sequence_300c",
])
def test_viewray_plan_public_content_changes_detected_against_source(tmp_path, tamper):
    source, destination = tmp_path / "source", tmp_path / "output"
    plan = _viewray_plan()
    if tamper == "sequence_300a":
        plan.DoseReferenceSequence = [Dataset()]
    elif tamper == "sequence_300c":
        plan.ReferencedDoseSequence = [Dataset()]
    write_dataset(plan, source / "synthetic.dcm")
    scan = scan_directory(source, allow_viewray_public_plan=True)
    report = process_batch(source, destination, options=_public_plan_options())
    assert report.ok, report.to_dict()
    path = next(destination.rglob("*.dcm"))
    saved = pydicom.dcmread(path)
    snapshot = capture_snapshot(saved)
    mapping = {}
    for tag_path, originals in scan.files[0].snapshot.entity_uids.items():
        mapping.update(zip(originals, snapshot.entity_uids[tag_path], strict=True))
    expected = [ExpectedOutput(scan.files[0], path, str(saved.PatientID), 0)]
    beam = saved.BeamSequence[0]
    if tamper == "gantry":
        beam.ControlPointSequence[0].GantryAngle = 91
    elif tamper == "mlc":
        beam.ControlPointSequence[0].BeamLimitingDevicePositionSequence[0].LeafJawPositions[0] = -11
    elif tamper == "weight":
        beam.ControlPointSequence[1].CumulativeMetersetWeight = 2
        beam.FinalCumulativeMetersetWeight = 2
    elif tamper == "referenced_beam":
        saved.ReferencedBeamSequence[0].ReferencedBeamNumber = 2
    elif tamper == "sequence_300a":
        saved.DoseReferenceSequence.append(Dataset())
    else:
        saved.ReferencedDoseSequence.append(Dataset())
    write_dataset(saved, path)
    findings = validate_batch(scan, expected, mapping, options=_public_plan_options())
    assert any(f.code == "CLINICAL_DATA_CHANGED" and f.severity == "FAIL" for f in findings)


def test_viewray_plan_requires_explicit_public_content_opt_in(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    write_dataset(_viewray_plan(), source / "synthetic.dcm")
    scan = scan_directory(source)
    assert not scan.ok
    assert any(f.code == "VIEWRAY_PLAN_REVIEW_REQUIRED" for f in scan.findings)
    report = process_batch(source, destination)
    assert not report.ok, report.to_dict()
    assert report.output_count == 0
    assert any(
        f.code == "VIEWRAY_PLAN_REVIEW_REQUIRED" and f.severity == "FAIL" for f in report.findings
    )
    assert not destination.exists()


@pytest.mark.parametrize("geometry", [
    "GantryAngle", "IsocenterPosition", "BeamLimitingDevicePositionSequence",
])
def test_viewray_plan_accepts_explicit_geometry_and_inherited_later_points(tmp_path, geometry):
    plan = _viewray_plan()
    beam = plan.BeamSequence[0]
    del beam.FinalCumulativeMetersetWeight
    for index, point in enumerate(beam.ControlPointSequence):
        for keyword in (
            "GantryAngle", "IsocenterPosition", "BeamLimitingDevicePositionSequence",
        ):
            if index or keyword != geometry:
                delattr(point, keyword)
    source, destination = tmp_path / "source", tmp_path / "output"
    write_dataset(plan, source / "synthetic.dcm")
    report = process_batch(source, destination, options=_public_plan_options())
    assert report.ok, report.to_dict()
    saved = pydicom.dcmread(next(destination.rglob("*.dcm")))
    assert len(saved.BeamSequence[0].ControlPointSequence) == 2
    assert geometry in saved.BeamSequence[0].ControlPointSequence[0]


def test_known_viewray_class_is_identified_without_disclosing_other_identifiers(tmp_path):
    plan = _viewray_plan()
    write_dataset(plan, tmp_path / "synthetic-patient-filename.dcm")
    result = inspect_storage(tmp_path, file_id=1)
    record = result["files"][0]
    assert record["status"] == "INSPECTED"
    assert record["supported_storage_class"] is True
    assert record["sop_class"]["uid"] == VIEWRAY_RT_PLAN_STORAGE
    assert "ViewRay" in record["sop_class"]["name"]
    assert record["file_meta"]["media_storage_sop_class"] == record["sop_class"]
    serialized = json.dumps(result)
    for private_value in (
        str(plan.SOPInstanceUID), str(plan.StudyInstanceUID), str(plan.PatientName),
        str(plan.PatientID), "synthetic-patient-filename", str(tmp_path),
    ):
        assert private_value not in serialized


@pytest.mark.parametrize("command", ["scan", "deidentify"])
def test_cli_requires_explicit_viewray_public_plan_option(tmp_path, capsys, command):
    source, destination = tmp_path / "source", tmp_path / "output"
    write_dataset(_viewray_plan(), source / "synthetic.dcm")
    arguments = [command, str(source)]
    if command == "deidentify":
        arguments.append(str(destination))
    assert main(arguments) == 1
    capsys.readouterr()
    assert not destination.exists()
    assert main([*arguments, "--viewray-public-plan"]) == 0
    text = capsys.readouterr().out
    if command == "scan":
        result = json.loads(text)
        assert result["dicom_files"] == 1
        assert any(f["code"] == "VIEWRAY_PRIVATE_PLAN" for f in result["findings"])
        assert not destination.exists()
    else:
        assert len(list(destination.rglob("*.dcm"))) == 1


def test_standalone_validation_rejects_viewray_plan_with_missing_public_beams(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    write_dataset(_viewray_plan(), source / "synthetic.dcm")
    report = process_batch(source, destination, options=_public_plan_options())
    assert report.ok, report.to_dict()
    path = next(destination.rglob("*.dcm"))
    saved = pydicom.dcmread(path)
    del saved.BeamSequence
    write_dataset(saved, path)
    result = validate_directory(destination)
    assert not result.ok, result.to_dict()
    assert any(f.code == "INVALID_STRUCTURE" and f.severity == "FAIL" for f in result.findings)


def test_batch_validation_without_viewray_opt_in_does_not_claim_metadata_pass(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "output"
    write_dataset(_viewray_plan(), source / "synthetic.dcm")
    scan = scan_directory(source, allow_viewray_public_plan=True)
    report = process_batch(source, destination, options=_public_plan_options())
    assert report.ok, report.to_dict()
    path = next(destination.rglob("*.dcm"))
    saved = pydicom.dcmread(path)
    snapshot = capture_snapshot(saved)
    mapping = {}
    for tag_path, originals in scan.files[0].snapshot.entity_uids.items():
        mapping.update(zip(originals, snapshot.entity_uids[tag_path], strict=True))
    expected = [ExpectedOutput(scan.files[0], path, str(saved.PatientID), 0)]
    findings = validate_batch(scan, expected, mapping, options=Options())
    assert any(
        f.code == "VIEWRAY_PLAN_REVIEW_REQUIRED" and f.severity == "FAIL" for f in findings
    )
    assert not any(f.code == "METADATA" and f.severity == "PASS" for f in findings)

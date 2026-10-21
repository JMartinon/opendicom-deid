from __future__ import annotations

import hashlib
import json
import shutil
from threading import Event

import pydicom
import pytest
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.inspection import capture_snapshot
from opendicom_deid.core.models import Options
from opendicom_deid.core.scanner import scan_directory


@pytest.mark.parametrize("modality", ["CT", "MR", "RTDOSE", "RTIMAGE"])
def test_basic_images_preserve_pixels_geometry_and_safe_metadata(
    tmp_path, dicom_factory, dicom_writer, modality
):
    source = tmp_path / "source"
    ds = dicom_factory(modality)
    ds.add_new(0x00111010, "LO", "PRIVATE SYNTHETIC ID")
    nested = Dataset()
    nested.add_new(0x00111011, "LO", "NESTED SYNTHETIC ID")
    ds.RequestAttributesSequence = [nested]
    path = dicom_writer(ds, source / "SYNTHETIC-NAME.dcm")
    before = hashlib.sha256(path.read_bytes()).digest()
    report = process_batch(source, tmp_path / "output")
    assert report.ok, report.to_dict()
    files = list((tmp_path / "output").rglob("*.dcm"))
    assert len(files) == 1
    result = pydicom.dcmread(files[0])
    assert result.PatientID == result.PatientName
    assert str(result.PatientID).startswith("SUBJECT-")
    assert result.PatientID != ds.PatientID
    assert result.SOPInstanceUID.startswith("2.25.")
    assert result.SOPInstanceUID != ds.SOPInstanceUID
    assert result.SOPClassUID == ds.SOPClassUID
    assert result.file_meta.TransferSyntaxUID == ds.file_meta.TransferSyntaxUID
    assert result.file_meta.MediaStorageSOPInstanceUID == result.SOPInstanceUID
    assert capture_snapshot(result).clinical == capture_snapshot(ds).clinical
    assert result.PixelData == ds.PixelData
    assert result.StudyDate == ""
    assert result.StudyTime == ""
    assert result.PatientBirthDate == ""
    assert all(not element.tag.is_private for element in result.iterall())
    assert result.preamble == b"\0" * 128
    assert hashlib.sha256(path.read_bytes()).digest() == before
    report_json = json.dumps(report.to_dict())
    for sensitive in [str(ds.PatientName), ds.PatientID, str(ds.SOPInstanceUID), str(source)]:
        assert sensitive not in report_json
    assert report.privacy_review == "REQUIRED"


def test_multiple_patients_and_multiple_studies_are_consistent(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    a = dicom_factory()
    b = dicom_factory("MR")
    c = dicom_factory(patient_id="SYNTHETIC-PATIENT-2", patient_name="Synthetic^Second")
    for i, ds in enumerate([a, b, c]):
        ds.InstanceNumber = i + 1
        dicom_writer(ds, source / f"{i}.dcm")
    report = process_batch(source, tmp_path / "output")
    assert report.ok, report.to_dict()
    results = {int(ds.InstanceNumber): ds for ds in (
        pydicom.dcmread(path) for path in (tmp_path / "output").glob("*.dcm")
    )}
    assert results[1].PatientID == results[2].PatientID
    assert results[1].PatientID != results[3].PatientID
    assert results[1].StudyInstanceUID != results[2].StudyInstanceUID


def test_nested_source_grouping_contains_only_neutral_path_names(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    for folder in ["SYNTHETIC-PATIENT/Plan", "SYNTHETIC-PATIENT/Doses/Run1", "SYNTHETIC-PATIENT/Doses/Run2"]:
        dicom_writer(dicom_factory(), source / folder / "SYNTHETIC-NAME.dcm")
    output = tmp_path / "output"
    report = process_batch(source, output, Options(organization="source"))
    assert report.ok, report.to_dict()
    files = list(output.rglob("*.dcm"))
    assert len(files) == 3
    assert len({path.parent for path in files}) == 3
    for path in files:
        relative = path.relative_to(output)
        assert all(part.startswith("group-") for part in relative.parts[:-1])
        assert path.name.startswith("2.25.")
        assert "SYNTHETIC" not in str(relative)


def test_recursive_option_and_non_dicom_files(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    dicom_writer(dicom_factory(), source / "a.dcm")
    dicom_writer(dicom_factory(), source / "nested" / "b.dcm")
    (source / "notes.txt").write_text("Non-DICOM synthetic note")
    scan = scan_directory(source, recursive=False)
    assert scan.total == 1
    assert scan.skipped == 1
    assert scan.ok
    report = process_batch(source, tmp_path / "output", Options(recursive=False))
    assert report.ok
    assert report.output_count == 1


def test_identical_duplicate_copies_retain_the_same_uid(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    path = dicom_writer(dicom_factory(), source / "a.dcm")
    shutil.copyfile(path, source / "b.dcm")
    report = process_batch(source, tmp_path / "output")
    assert report.ok, report.to_dict()
    results = [pydicom.dcmread(path) for path in (tmp_path / "output").glob("*.dcm")]
    assert len(results) == 2
    assert results[0].SOPInstanceUID == results[1].SOPInstanceUID
    assert any(f.code == "DUPLICATE_COPY" for f in report.findings)


def test_conflicting_duplicates_fail_without_publishing(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    ds = dicom_factory()
    dicom_writer(ds, source / "a.dcm")
    ds.PixelData = b"\x00" * 8
    dicom_writer(ds, source / "b.dcm")
    report = process_batch(source, tmp_path / "output")
    assert not report.ok
    assert any(f.code == "CONFLICTING_DUPLICATE" for f in report.findings)
    assert not (tmp_path / "output").exists()


def test_dose_without_grid_is_valid(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    ds = dicom_factory("RTDOSE", pixels=False)
    roi = Dataset()
    roi.ROINumber = 1
    roi.ROIName = "Synthetic point"
    roi.ReferencedFrameOfReferenceUID = ds.FrameOfReferenceUID
    ds.StructureSetROISequence = [roi]
    point = Dataset()
    point.ContourGeometricType = "POINT"
    point.NumberOfContourPoints = 1
    point.ContourData = [1.2, 3.4, 5.6]
    contour = Dataset()
    contour.ReferencedROINumber = 1
    contour.ContourSequence = [point]
    ds.ROIContourSequence = [contour]
    dose = Dataset()
    dose.ReferencedROINumber = 1
    dose.DoseUnits = "GY"
    dose.DoseValue = 2.5
    ds.RTDoseROISequence = [dose]
    dicom_writer(ds, source / "point.dcm")
    report = process_batch(source, tmp_path / "output")
    assert report.ok, report.to_dict()
    result = pydicom.dcmread(next((tmp_path / "output").glob("*.dcm")))
    assert "PixelData" not in result
    assert "Rows" not in result
    assert result.ROIContourSequence[0].ContourSequence[0].ContourData == [1.2, 3.4, 5.6]
    assert result.RTDoseROISequence[0].DoseValue == 2.5


def test_shift_is_nonzero_consistent_and_preserves_intervals(tmp_path, dicom_factory, dicom_writer):
    from datetime import datetime

    source = tmp_path / "source"
    for i, study_date in enumerate(["20240131", "20240202"]):
        ds = dicom_factory()
        ds.StudyDate = study_date
        ds.InstanceNumber = i + 1
        dicom_writer(ds, source / f"{i}.dcm")
    report = process_batch(source, tmp_path / "output", Options(date_mode="shift"))
    assert report.ok, report.to_dict()
    output = sorted((pydicom.dcmread(path) for path in (tmp_path / "output").glob("*.dcm")),
                    key=lambda ds: int(ds.InstanceNumber))
    shifts = [(datetime.strptime(str(ds.StudyDate), "%Y%m%d") - datetime.strptime(original, "%Y%m%d")).days
              for ds, original in zip(output, ["20240131", "20240202"], strict=True)]
    assert shifts[0] == shifts[1] != 0
    assert all(ds.PatientBirthDate == "" for ds in output)
    assert all(ds.StudyTime == "121314.25" for ds in output)
    assert all(str(ds.AcquisitionDateTime).endswith("121314.25-0500") for ds in output)


@pytest.mark.parametrize("when", ["before", "during"])
def test_cancellation_removes_incomplete_output(tmp_path, dicom_factory, dicom_writer, monkeypatch, when):
    from opendicom_deid.core import anonymizer

    source = tmp_path / "source"
    for i in range(3):
        dicom_writer(dicom_factory(), source / f"{i}.dcm")
    cancel = Event()
    original_write = anonymizer._write
    def cancelling_write(ds, target):
        original_write(ds, target)
        cancel.set()
    if when == "before":
        cancel.set()
    else:
        monkeypatch.setattr(anonymizer, "_write", cancelling_write)
    report = process_batch(source, tmp_path / "output", cancel=cancel)
    assert report.status == "CANCELLED"
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))
    assert len(list(source.glob("*.dcm"))) == 3


def test_write_failure_does_not_publish_partial_output(tmp_path, dicom_factory, dicom_writer, monkeypatch):
    from opendicom_deid.core import anonymizer

    source = tmp_path / "source"
    for i in range(2):
        dicom_writer(dicom_factory(), source / f"{i}.dcm")
    count = 0
    original_write = anonymizer._write
    def failing_write(ds, target):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("SYNTHETIC sensitive failure path must not be logged")
        original_write(ds, target)
    monkeypatch.setattr(anonymizer, "_write", failing_write)
    report = process_batch(source, tmp_path / "output")
    assert not report.ok
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))
    assert "sensitive failure" not in json.dumps(report.to_dict())


@pytest.mark.parametrize("location", ["same", "inside", "parent", "nonempty"])
def test_unsafe_destinations_are_rejected(tmp_path, dicom_factory, dicom_writer, location):
    source = tmp_path / "source"
    path = dicom_writer(dicom_factory(), source / "a.dcm")
    original = path.read_bytes()
    destination = {"same": source, "inside": source / "output", "parent": tmp_path,
                   "nonempty": tmp_path / "occupied"}[location]
    if location == "nonempty":
        destination.mkdir()
        (destination / "keep.txt").write_text("keep")
    report = process_batch(source, destination)
    assert not report.ok
    assert path.read_bytes() == original


def test_unsupported_sop_class_fails(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    ds = dicom_factory()
    ds.SOPClassUID = generate_uid()
    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    dicom_writer(ds, source / "unsupported.dcm")
    report = process_batch(source, tmp_path / "output")
    assert not report.ok
    assert any(f.code == "UNSUPPORTED_OBJECT" for f in report.findings)


def test_missing_external_reference_is_remapped_and_reported(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    ds = dicom_factory("RTDOSE")
    external_uid = generate_uid()
    ref = Dataset()
    ref.ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.481.5"
    ref.ReferencedSOPInstanceUID = external_uid
    ds.ReferencedRTPlanSequence = [ref]
    dicom_writer(ds, source / "dose.dcm")
    report = process_batch(source, tmp_path / "output")
    assert report.ok, report.to_dict()
    assert any(f.code == "PREEXISTING_MISSING_REFERENCES" for f in report.findings)
    result = pydicom.dcmread(next((tmp_path / "output").glob("*.dcm")))
    assert result.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID != external_uid

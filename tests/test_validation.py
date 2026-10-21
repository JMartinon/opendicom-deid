from __future__ import annotations

import json
import struct

import pydicom
import pytest
from conftest import reference
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.inspection import capture_snapshot
from opendicom_deid.core.models import ExpectedOutput, Options, ScanResult
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.validator import validate_batch, validate_directory


def prepared_batch(tmp_path, dicom_factory, dicom_writer):
    source, destination = tmp_path / "source", tmp_path / "output"
    image = dicom_factory()
    struct = dicom_factory("RTSTRUCT", study=str(image.StudyInstanceUID), frame=str(image.FrameOfReferenceUID))
    struct.ReferencedImageSequence = [reference(image)]
    dicom_writer(image, source / "a.dcm")
    dicom_writer(struct, source / "b.dcm")
    scan = scan_directory(source)
    report = process_batch(source, destination)
    assert report.ok, report.to_dict()
    files = sorted(destination.glob("*.dcm"), key=lambda path: path.stem.rsplit("-", 1)[1])
    expected, mapping = [], {}
    for record, path in zip(scan.files, files, strict=True):
        ds = pydicom.dcmread(path)
        snap = capture_snapshot(ds)
        for tag_path, originals in record.snapshot.entity_uids.items():
            mapping.update(zip(originals, snap.entity_uids[tag_path], strict=True))
        expected.append(ExpectedOutput(record, path, str(ds.PatientID), 0))
    return scan, expected, mapping


@pytest.mark.parametrize("tamper,code", [
    ("pixels", "CLINICAL_DATA_CHANGED"), ("geometry", "CLINICAL_DATA_CHANGED"),
    ("identity", "PATIENT_PSEUDONYM"), ("private", "PRIVATE_TAGS"),
    ("date", "DATE_POLICY"), ("free_text", "RESIDUAL_METADATA"),
    ("technical_uid", "TECHNICAL_UID_CHANGED"), ("preamble", "RESIDUAL_FILE_METADATA"),
    ("file_meta_text", "RESIDUAL_FILE_METADATA"),
])
def test_independent_validator_detects_tampered_outputs(
    tmp_path, dicom_factory, dicom_writer, tamper, code
):
    scan, expected, mapping = prepared_batch(tmp_path, dicom_factory, dicom_writer)
    path = expected[0].path
    ds = pydicom.dcmread(path)
    if tamper == "pixels":
        ds.PixelData = b"\0" * 8
    elif tamper == "geometry":
        ds.PixelSpacing = [2, 3]
    elif tamper == "identity":
        ds.PatientID = "SYNTHETIC-LEAK"
    elif tamper == "private":
        ds.add_new(0x00111011, "LO", "SYNTHETIC-LEAK")
    elif tamper == "date":
        ds.StudyDate = "20240101"
    elif tamper == "free_text":
        ds.ImageComments = "SYNTHETIC-LEAK"
    elif tamper == "technical_uid":
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    elif tamper == "preamble":
        ds.preamble = b"SYNTHETIC-LEAK".ljust(128, b"\0")
    elif tamper == "file_meta_text":
        ds.file_meta.SourceApplicationEntityTitle = "SYNTHETIC-LEAK"
    dicom_writer(ds, path)
    findings = validate_batch(scan, expected, mapping, options=Options())
    assert any(f.code == code and f.severity == "FAIL" for f in findings), findings
    assert "SYNTHETIC-LEAK" not in json.dumps([f.__dict__ for f in findings])


def test_reference_sop_class_mismatch_is_rejected(tmp_path, dicom_factory, dicom_writer):
    scan, expected, mapping = prepared_batch(tmp_path, dicom_factory, dicom_writer)
    path = expected[1].path
    ds = pydicom.dcmread(path)
    ds.ReferencedImageSequence[0].ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
    dicom_writer(ds, path)
    findings = validate_batch(scan, expected, mapping, options=Options())
    assert any(f.code == "REFERENCE_CLASS_MISMATCH" for f in findings)


def test_missing_file_fails_readability_and_graph(tmp_path, dicom_factory, dicom_writer):
    scan, expected, mapping = prepared_batch(tmp_path, dicom_factory, dicom_writer)
    expected[0].path.unlink()
    findings = validate_batch(scan, expected, mapping, options=Options())
    assert any(f.code == "OUTPUT_UNREADABLE" for f in findings)
    assert any(f.code == "BROKEN_REFERENCES" for f in findings)
    assert not any(f.code in {"REFERENCE_GRAPH", "READABILITY"} and f.severity == "PASS" for f in findings)


def test_file_count_mismatch_fails(tmp_path, dicom_factory, dicom_writer):
    scan, expected, mapping = prepared_batch(tmp_path, dicom_factory, dicom_writer)
    findings = validate_batch(scan, expected[:1], mapping, options=Options())
    assert any(f.code == "FILE_COUNT" for f in findings)


def test_duplicate_output_record_cannot_mask_missing_source(tmp_path, dicom_factory, dicom_writer):
    scan, expected, mapping = prepared_batch(tmp_path, dicom_factory, dicom_writer)
    findings = validate_batch(scan, [expected[0], expected[0]], mapping, options=Options())
    assert any(f.code == "FILE_COUNT" for f in findings)


def test_empty_batch_has_no_pass_findings(tmp_path):
    findings = validate_batch(ScanResult(tmp_path), [], {}, options=Options())
    assert any(f.code == "NO_DICOM_FILES" for f in findings)
    assert not any(f.severity == "PASS" for f in findings)


def test_alternate_instance_reference_is_explicitly_missing(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    image = dicom_factory()
    original = generate_uid()
    image.SOPInstanceUIDOfConcatenationSource = original
    dicom_writer(image, source / "a.dcm")
    report = process_batch(source, tmp_path / "output")
    assert report.ok, report.to_dict()
    assert any(f.code == "PREEXISTING_MISSING_REFERENCES" for f in report.findings)
    saved = pydicom.dcmread(next((tmp_path / "output").glob("*.dcm")))
    assert saved.SOPInstanceUIDOfConcatenationSource != original


def test_independent_directory_validation_requires_source_and_privacy_review(
    tmp_path, dicom_factory, dicom_writer
):
    _, expected, _ = prepared_batch(tmp_path, dicom_factory, dicom_writer)
    report = validate_directory(expected[0].path.parent)
    assert report.ok, report.to_dict()
    assert any(f.code == "NO_SOURCE_COMPARISON" for f in report.findings)
    assert report.privacy_review == "REQUIRED"
    assert not any(f.code == "CLINICAL_PRESERVATION" for f in report.findings)


def test_directory_validation_checks_pixel_structure(tmp_path, dicom_factory, dicom_writer):
    _, expected, _ = prepared_batch(tmp_path, dicom_factory, dicom_writer)
    ds = pydicom.dcmread(expected[0].path)
    del ds.PixelData
    dicom_writer(ds, expected[0].path)
    report = validate_directory(expected[0].path.parent)
    assert not report.ok
    assert any(f.code == "INVALID_STRUCTURE" for f in report.findings)


def test_directory_validator_finds_missing_reference_without_source(
    tmp_path, dicom_factory, dicom_writer
):
    _, expected, _ = prepared_batch(tmp_path, dicom_factory, dicom_writer)
    expected[0].path.unlink()
    report = validate_directory(expected[1].path.parent)
    assert report.ok
    assert any(f.code == "UNRESOLVED_REFERENCES" for f in report.findings)


def test_directory_validator_does_not_pass_graph_when_unreadable(tmp_path, dicom_factory, dicom_writer):
    _, expected, _ = prepared_batch(tmp_path, dicom_factory, dicom_writer)
    expected[0].path.write_bytes(b"corrupted")
    report = validate_directory(expected[0].path.parent)
    assert not report.ok
    assert not any(f.code == "REFERENCE_GRAPH" and f.severity == "PASS" for f in report.findings)


def test_file_meta_instance_uid_mismatch_is_detected(tmp_path, dicom_factory, dicom_writer):
    scan, expected, mapping = prepared_batch(tmp_path, dicom_factory, dicom_writer)
    path = expected[0].path
    ds = pydicom.dcmread(path)
    old_uid = str(ds.SOPInstanceUID)
    replacement = "2.25." + "1" * (len(old_uid) - 5)
    data = path.read_bytes()
    # Corrupt only the first occurrence (file meta); normal dcmwrite repairs this field.
    assert data.count(old_uid.encode()) >= 2
    path.write_bytes(data.replace(old_uid.encode(), replacement.encode(), 1))
    findings = validate_batch(scan, expected, mapping, options=Options())
    assert any(f.code == "FILE_META_MISMATCH" for f in findings)


def test_preserved_privacy_history_is_excluded_from_live_graph(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    ds = dicom_factory()
    history = Dataset()
    history.SOPInstanceUID = generate_uid()
    history.PatientName = "Historical^Synthetic"
    history.ImagePositionPatient = [8, 9, 10]
    ds.OriginalAttributesSequence = [history]
    dicom_writer(ds, source / "history.dcm")
    report = process_batch(source, tmp_path / "output")
    assert report.ok, report.to_dict()
    result = pydicom.dcmread(next((tmp_path / "output").glob("*.dcm")))
    assert "OriginalAttributesSequence" not in result


def test_malformed_source_uid_does_not_leak_parser_logging(tmp_path, dicom_factory, dicom_writer, caplog, capsys):
    source = tmp_path / "source"
    path = dicom_writer(dicom_factory(), source / "synthetic.dcm")
    ds = pydicom.dcmread(path)
    uid = str(ds.StudyInstanceUID)
    sentinel = "SYNTHETIC_PRIVATE_SENTINEL".ljust(len(uid), "X")
    path.write_bytes(path.read_bytes().replace(uid.encode(), sentinel.encode()))
    caplog.clear()
    report = process_batch(source, tmp_path / "output")
    captured = capsys.readouterr()
    assert not report.ok
    assert sentinel not in caplog.text + captured.out + captured.err + json.dumps(report.to_dict())


def test_spectroscopy_payload_preservation_is_verified(tmp_path, dicom_factory, dicom_writer):
    source, destination = tmp_path / "source", tmp_path / "output"
    ds = dicom_factory("MR", pixels=False)
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.4.2"
    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    ds.SpectroscopyData = struct.pack("<4f", 1, 2, 3, 4)
    dicom_writer(ds, source / "spectroscopy.dcm")
    scan = scan_directory(source)
    report = process_batch(source, destination)
    assert report.ok, report.to_dict()
    path = next(destination.glob("*.dcm"))
    output = pydicom.dcmread(path)
    assert output.SpectroscopyData == ds.SpectroscopyData
    saved = capture_snapshot(output)
    mapping = {}
    for key, originals in scan.files[0].snapshot.entity_uids.items():
        mapping.update(zip(originals, saved.entity_uids[key], strict=True))
    expected = [ExpectedOutput(scan.files[0], path, str(output.PatientID), 0)]
    output.SpectroscopyData = struct.pack("<4f", 4, 3, 2, 1)
    dicom_writer(output, path)
    findings = validate_batch(scan, expected, mapping, options=Options())
    assert any(f.code == "CLINICAL_DATA_CHANGED" for f in findings)


@pytest.mark.parametrize("keyword", ["ExtendedOffsetTable", "ExtendedOffsetTableLengths"])
def test_extended_frame_offsets_are_fingerprinted(dicom_factory, keyword):
    ds = dicom_factory()
    setattr(ds, keyword, struct.pack("<2Q", 0, 12))
    before = capture_snapshot(ds)
    setattr(ds, keyword, struct.pack("<2Q", 0, 24))
    assert capture_snapshot(ds).clinical != before.clinical

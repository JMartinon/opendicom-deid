"""SOP classes define modality and pixel requirements for synthetic inputs."""

import json
import warnings

import pydicom
import pytest
from pydicom.uid import (
    DigitalXRayImageStorageForPresentation,
    DigitalXRayImageStorageForProcessing,
    MultiFrameGrayscaleByteSecondaryCaptureImageStorage,
    MultiFrameGrayscaleWordSecondaryCaptureImageStorage,
    MultiFrameSingleBitSecondaryCaptureImageStorage,
    MultiFrameTrueColorSecondaryCaptureImageStorage,
    SecondaryCaptureImageStorage,
    XRayAngiographicImageStorage,
)

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.inspection import capture_snapshot
from opendicom_deid.core.models import ExpectedOutput, Options
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.validator import validate_batch, validate_directory

SC_CLASSES = (
    SecondaryCaptureImageStorage,
    MultiFrameSingleBitSecondaryCaptureImageStorage,
    MultiFrameGrayscaleByteSecondaryCaptureImageStorage,
    MultiFrameGrayscaleWordSecondaryCaptureImageStorage,
    MultiFrameTrueColorSecondaryCaptureImageStorage,
)


def secondary_capture(dicom_factory, sop_class=SecondaryCaptureImageStorage):
    """Minimal generated pixels appropriate to each supported SC encoding."""
    ds = dicom_factory()
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID = sop_class
    ds.ConversionType = "SYN"
    ds.PixelRepresentation = 0
    if sop_class != SecondaryCaptureImageStorage:
        ds.NumberOfFrames = 2
        ds.PixelData *= 2
    if sop_class == MultiFrameSingleBitSecondaryCaptureImageStorage:
        ds.BitsAllocated = ds.BitsStored = 1
        ds.HighBit = 0
        ds.PixelData = b"\x55\x00"
    elif sop_class in {
        MultiFrameGrayscaleByteSecondaryCaptureImageStorage,
        MultiFrameTrueColorSecondaryCaptureImageStorage,
    }:
        ds.BitsAllocated = ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelData = bytes(range(8))
    if sop_class == MultiFrameTrueColorSecondaryCaptureImageStorage:
        ds.SamplesPerPixel = 3
        ds.PlanarConfiguration = 0
        ds.PhotometricInterpretation = "RGB"
        ds.PixelData = bytes(range(24))
    return ds


@pytest.mark.parametrize("sop_class", SC_CLASSES)
@pytest.mark.parametrize("modality", ["MG", "RF"])
def test_secondary_capture_accepts_originating_modality_and_preserves_pixels(
    tmp_path, dicom_factory, dicom_writer, sop_class, modality,
):
    ds = secondary_capture(dicom_factory, sop_class)
    ds.Modality = modality
    source, output = tmp_path / "source", tmp_path / "output"
    dicom_writer(ds, source / "synthetic.dcm")

    scan = scan_directory(source)
    assert scan.ok, scan.findings
    assert scan.modalities == {modality: 1}
    report = process_batch(source, output)
    assert report.ok, report.to_dict()
    saved = pydicom.dcmread(next(output.glob("*.dcm")))
    assert saved.Modality == modality
    assert saved.SOPClassUID == sop_class
    assert saved.PixelData == ds.PixelData
    independent = validate_directory(output)
    assert independent.ok, independent.to_dict()
    assert independent.modalities == {modality: 1}
    assert f"{modality}: 1" in independent.summary()


@pytest.mark.parametrize("modality", [None, ""])
def test_secondary_capture_allows_absent_or_empty_optional_modality(
    tmp_path, dicom_factory, dicom_writer, modality,
):
    ds = secondary_capture(dicom_factory)
    if modality is None:
        del ds.Modality
    else:
        ds.Modality = modality
    source, output = tmp_path / "source", tmp_path / "output"
    dicom_writer(ds, source / "synthetic.dcm")
    report = process_batch(source, output)
    assert report.ok, report.to_dict()
    saved = pydicom.dcmread(next(output.glob("*.dcm")))
    assert ("Modality" in saved) == (modality is not None)
    assert saved.get("Modality") == modality
    assert saved.PixelData == ds.PixelData


def test_custom_valid_secondary_capture_modality_is_preserved_without_report_echo(
    tmp_path, dicom_factory, dicom_writer,
):
    ds = secondary_capture(dicom_factory)
    ds.Modality = "SYNTHETIC_CUSTOM"
    source, output = tmp_path / "source", tmp_path / "output"
    dicom_writer(ds, source / "synthetic.dcm")
    scan = scan_directory(source)
    assert scan.ok, scan.findings
    assert scan.modalities == {"Other modality": 1}
    report = process_batch(source, output)
    assert report.ok, report.to_dict()
    assert "SYNTHETIC_CUSTOM" not in json.dumps(report.to_dict())
    assert "SYNTHETIC_CUSTOM" not in report.summary()
    saved = pydicom.dcmread(next(output.glob("*.dcm")))
    assert saved.Modality == "SYNTHETIC_CUSTOM"
    independent = validate_directory(output)
    assert independent.ok, independent.to_dict()
    assert independent.modalities == {"Other modality": 1}
    assert "SYNTHETIC_CUSTOM" not in json.dumps(independent.to_dict())
    assert "SYNTHETIC_CUSTOM" not in independent.summary()


@pytest.mark.parametrize("modality", [None, "", "MR"])
def test_non_secondary_capture_rejects_missing_or_inconsistent_modality(
    tmp_path, dicom_factory, dicom_writer, modality,
):
    ds = dicom_factory("CT")
    if modality is None:
        del ds.Modality
    else:
        ds.Modality = modality
    source, output = tmp_path / "source", tmp_path / "output"
    dicom_writer(ds, source / "synthetic.dcm")
    report = process_batch(source, output)
    assert not report.ok
    assert any(f.code == "MODALITY" and f.severity == "FAIL" for f in report.findings)
    assert not output.exists()


@pytest.mark.parametrize("modality", [
    "Synthetic^Leak", "SYNTHETIC_LEAK_TOO_LONG", ["MG", "SYNTHETIC_LEAK"],
])
def test_secondary_capture_rejects_invalid_cs_or_multiple_modalities_without_echo(
    tmp_path, dicom_factory, dicom_writer, modality, capsys,
):
    ds = secondary_capture(dicom_factory)
    source, output = tmp_path / "source", tmp_path / "output"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        ds.Modality = modality
        dicom_writer(ds, source / "synthetic.dcm")
    report = process_batch(source, output)
    assert not report.ok
    assert any(f.code in {"MODALITY", "DICOM_READ"} and f.severity == "FAIL"
               for f in report.findings)
    assert not output.exists()
    exposed = json.dumps(report.to_dict()) + report.summary() + capsys.readouterr().err
    assert "SYNTHETIC_LEAK" not in exposed
    assert "Synthetic^Leak" not in exposed


@pytest.mark.parametrize("modality", ["RTPLAN", "RTSTRUCT", "RTDOSE"])
def test_secondary_capture_rt_modality_cannot_bypass_required_pixel_data(
    tmp_path, dicom_factory, dicom_writer, modality,
):
    ds = secondary_capture(dicom_factory)
    ds.Modality = modality
    del ds.PixelData
    source, output = tmp_path / "source", tmp_path / "output"
    dicom_writer(ds, source / "synthetic.dcm")
    report = process_batch(source, output)
    assert not report.ok
    assert any(f.code == "INVALID_STRUCTURE" for f in report.findings)
    assert not output.exists()


def test_secondary_capture_dose_modality_does_not_require_grid_dose_metadata(
    tmp_path, dicom_factory, dicom_writer,
):
    ds = secondary_capture(dicom_factory)
    ds.Modality = "RTDOSE"
    assert "DoseGridScaling" not in ds
    source, output = tmp_path / "source", tmp_path / "output"
    dicom_writer(ds, source / "synthetic.dcm")
    report = process_batch(source, output)
    assert report.ok, report.to_dict()
    saved = pydicom.dcmread(next(output.glob("*.dcm")))
    assert saved.Modality == "RTDOSE"
    assert "DoseGridScaling" not in saved
    assert saved.PixelData == ds.PixelData


def test_actual_grid_dose_still_requires_positive_scaling(
    tmp_path, dicom_factory, dicom_writer,
):
    ds = dicom_factory("RTDOSE")
    del ds.DoseGridScaling
    source, output = tmp_path / "source", tmp_path / "output"
    dicom_writer(ds, source / "synthetic.dcm")
    report = process_batch(source, output)
    assert not report.ok
    assert any(f.code == "INVALID_STRUCTURE" for f in report.findings)
    assert not output.exists()


@pytest.mark.parametrize("sop_class,modality", [
    (sop_class, modality)
    for sop_class in (
        DigitalXRayImageStorageForPresentation, DigitalXRayImageStorageForProcessing,
    )
    for modality in ("MG", "PX", "IO")
] + [(XRayAngiographicImageStorage, "RF")])
def test_supported_xray_classes_accept_standard_modality_alternatives(
    tmp_path, dicom_factory, dicom_writer, sop_class, modality,
):
    ds = dicom_factory()
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID = sop_class
    ds.Modality = modality
    dicom_writer(ds, tmp_path / "synthetic.dcm")
    scan = scan_directory(tmp_path)
    assert scan.ok, scan.findings
    assert scan.total == 1
    assert scan.modalities == {modality: 1}


@pytest.mark.parametrize("source_kind,replacement", [("secondary_capture", "OT"), ("CT", "MR")])
def test_independent_comparison_rejects_changed_modality(
    tmp_path, dicom_factory, dicom_writer, source_kind, replacement,
):
    if source_kind == "secondary_capture":
        ds = secondary_capture(dicom_factory)
        ds.Modality = "MG"
    else:
        ds = dicom_factory("CT")
    source, output = tmp_path / "source", tmp_path / "output"
    dicom_writer(ds, source / "synthetic.dcm")
    scan = scan_directory(source)
    report = process_batch(source, output)
    assert report.ok, report.to_dict()
    path = next(output.glob("*.dcm"))
    saved = pydicom.dcmread(path)
    snapshot = capture_snapshot(saved)
    expected = ExpectedOutput(scan.files[0], path, str(saved.PatientID), 0)
    mapping = {}
    for tag_path, original_uids in scan.files[0].snapshot.entity_uids.items():
        mapping.update(zip(original_uids, snapshot.entity_uids[tag_path], strict=True))

    saved.Modality = replacement
    dicom_writer(saved, path)
    findings = validate_batch(scan, [expected], mapping, options=Options())
    assert any(f.code == "CLINICAL_DATA_CHANGED" and f.severity == "FAIL" for f in findings)
    assert not any(f.code == "CLINICAL_PRESERVATION" for f in findings)
    if source_kind == "CT":
        assert any(f.code == "MODALITY" and f.severity == "FAIL" for f in findings)
        assert not any(f.code == "METADATA" and f.severity == "PASS" for f in findings)

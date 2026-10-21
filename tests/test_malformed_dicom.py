import struct

import pydicom
import pytest

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.reader import read_dicom


def test_viewray_private_fd_wrong_length_removed_before_conversion(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    path = dicom_writer(dicom_factory(), source / "malformed.dcm")
    with path.open("ab") as stream:
        stream.write(struct.pack("<HH2sH", 0x01F1, 0x0010, b"LO", 8) + b"VIEWRAY ")
        stream.write(struct.pack("<HH2sH", 0x01F1, 0x1026, b"FD", 6) + b"0.938 ")
    previous_config = pydicom.config.convert_wrong_length_to_UN
    pydicom.config.convert_wrong_length_to_UN = False
    try:
        with pytest.raises(pydicom.errors.BytesLengthException):
            _ = pydicom.dcmread(path)[0x01F11026].value
        report = process_batch(source, tmp_path / "output")
        assert report.ok, report.to_dict()
        assert pydicom.config.convert_wrong_length_to_UN is False
        result = read_dicom(next((tmp_path / "output").glob("*.dcm")), remove_private=False)
        assert all(not element.tag.is_private for element in result.iterall())
    finally:
        pydicom.config.convert_wrong_length_to_UN = previous_config


def test_malformed_public_fd_fails_closed(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    path = dicom_writer(dicom_factory(), source / "malformed.dcm")
    with path.open("ab") as stream:
        stream.write(struct.pack("<HH2sH", 0x0018, 0x9087, b"FD", 6) + b"0.938 ")
    report = process_batch(source, tmp_path / "output")
    assert not report.ok
    assert not (tmp_path / "output").exists()
    assert any(f.code == "DICOM_READ" for f in report.findings)


def test_unreadable_dicom_candidate_blocks_entire_batch(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "source"
    dicom_writer(dicom_factory(), source / "good.dcm")
    (source / "broken.dcm").write_bytes(b"not a valid DICOM file")
    report = process_batch(source, tmp_path / "output")
    assert not report.ok
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("invalid", ["missing_pixels", "truncated_pixels", "zero_dose_scaling"])
def test_invalid_image_structure_fails_before_publication(tmp_path, dicom_factory, dicom_writer, invalid):
    source = tmp_path / "source"
    ds = dicom_factory("RTDOSE" if invalid == "zero_dose_scaling" else "CT")
    if invalid == "missing_pixels":
        del ds.PixelData
    elif invalid == "truncated_pixels":
        ds.PixelData = b"\0\0"
    else:
        ds.DoseGridScaling = 0
    dicom_writer(ds, source / "bad.dcm")
    report = process_batch(source, tmp_path / "output")
    assert not report.ok
    assert any(f.code == "INVALID_STRUCTURE" for f in report.findings)
    assert not (tmp_path / "output").exists()

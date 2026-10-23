"""Synthetic metadata-policy checks; all names and identifiers are invented."""

from __future__ import annotations

import struct
from copy import deepcopy

import pytest
from pydicom import dcmwrite
from pydicom.dataelem import DataElement, RawDataElement
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.tag import Tag
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, JPEGBaseline8Bit

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.policy import PolicyError, metadata_issues, scrub_metadata
from opendicom_deid.core.reader import ReadError, read_dicom
from opendicom_deid.core.structure import structural_error

ORIGINAL = "1.2.826.0.1.3680043.10.999.1"
REFERENCED = "1.2.826.0.1.3680043.10.999.2"
MAPPING = {ORIGINAL: "2.25.101", REFERENCED: "2.25.102"}


def minimal_dataset() -> Dataset:
    dataset = Dataset()
    dataset.PatientName = "Synthetic^Person"
    dataset.PatientID = "SYNTHETIC-ONLY"
    dataset.SOPClassUID = CTImageStorage
    dataset.SOPInstanceUID = ORIGINAL
    dataset.PatientBirthDate = "19900101"
    dataset.PatientBirthTime = "123456"
    return dataset


def apply(dataset: Dataset, **kwargs) -> None:
    scrub_metadata(dataset, pseudonym="SUBJECT-TEST", uid_mapping=MAPPING, **kwargs)


def test_recursive_reference_containers_keep_classes_and_clinical_numbers():
    dataset = minimal_dataset()
    reference = Dataset()
    reference.ReferencedSOPClassUID = CTImageStorage
    reference.ReferencedSOPInstanceUID = REFERENCED
    reference.PatientID = "SYNTHETIC-NESTED"
    reference.PatientName = "Synthetic^Nested"
    reference.ContourData = [1.25, 2.5, 3.75, -4, 5, 6]
    reference.NumberOfContourPoints = 2
    wrapped = Dataset()
    wrapped.ReferencedImageSequence = Sequence([reference])
    dataset.ReferencedStudySequence = Sequence([wrapped])
    dataset.PixelData = b"\x01\x00\x02\x00"
    dataset.DoseGridScaling = "0.000125"
    dataset.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    original_contour = deepcopy(reference.ContourData)
    apply(dataset)
    output = dataset.ReferencedStudySequence[0].ReferencedImageSequence[0]
    assert output.ReferencedSOPClassUID == CTImageStorage
    assert output.ReferencedSOPInstanceUID == MAPPING[REFERENCED]
    assert output.PatientName == output.PatientID == "SUBJECT-TEST"
    assert output.ContourData == original_contour
    assert output.NumberOfContourPoints == 2
    assert dataset.PixelData == b"\x01\x00\x02\x00"
    assert dataset.DoseGridScaling == 0.000125
    assert dataset.ImageOrientationPatient == [1, 0, 0, 0, 1, 0]
    assert not metadata_issues(dataset, pseudonym="SUBJECT-TEST")


@pytest.mark.parametrize("keyword,value", [
    ("StudyDate", "20240229"),
    ("AcquisitionDateTime", "20240229123456.125+0100"),
    ("StudyTime", "123456.125"),
    ("RTPlanDate", "20240229"),
    ("RTPlanTime", "123456"),
])
def test_default_policy_clears_every_date_time(keyword, value):
    dataset = minimal_dataset()
    setattr(dataset, keyword, value)
    apply(dataset)
    assert getattr(dataset, keyword) == ""
    assert dataset.PatientBirthDate == dataset.PatientBirthTime == ""
    assert dataset.LongitudinalTemporalInformationModified == "REMOVED"


@pytest.mark.parametrize("keyword,value,offset,expected", [
    ("StudyDate", "20240228", 1, "20240229"),
    ("StudyDate", "20240301", -1, "20240229"),
    ("StudyDate", "20240101", -1, "20231231"),
    ("AcquisitionDateTime", "20241231235959.123456-0500", 1, "20250101235959.123456-0500"),
    ("AcquisitionDateTime", "2024123123", 1, "2025010123"),
    ("AcquisitionDateTime", "20241231+0100", 1, "20250101+0100"),
    ("StudyTime", "123456.1234", -2, "123456.1234"),
    ("StudyDate", "", 1, ""),
])
def test_shift_preserves_precision_and_clock_while_removing_birth_fields(keyword, value, offset, expected):
    dataset = minimal_dataset()
    setattr(dataset, keyword, value)
    apply(dataset, date_mode="shift", date_offset_days=offset)
    assert getattr(dataset, keyword) == expected
    assert dataset.PatientBirthDate == dataset.PatientBirthTime == ""
    assert dataset.LongitudinalTemporalInformationModified == "MODIFIED"
    assert not metadata_issues(dataset, pseudonym="SUBJECT-TEST", date_mode="shift")


@pytest.mark.parametrize("vr,tag,value", [
    ("DT", 0x0008002A, "2024"),
    ("DT", 0x0008002A, "202401"),
    ("DT", 0x0008002A, "20240101240000"),
    ("DT", 0x0008002A, "20240101000000+1460"),
    ("DA", 0x00080020, "20240231"),
    ("TM", 0x00080030, "256000"),
])
def test_unshiftable_dates_fail_without_echoing_the_value(vr, tag, value):
    dataset = minimal_dataset()
    # Deliberately bypass constructor validation to exercise hostile input paths.
    element = DataElement(tag, vr, "")
    element._value = value
    dataset.add(element)
    with pytest.raises(PolicyError) as error:
        apply(dataset, date_mode="shift", date_offset_days=1)
    assert value not in str(error.value)


def test_shift_overflow_and_zero_offset_fail():
    dataset = minimal_dataset()
    dataset.StudyDate = "99991231"
    with pytest.raises(PolicyError):
        apply(dataset, date_mode="shift", date_offset_days=1)
    with pytest.raises(PolicyError):
        apply(minimal_dataset(), date_mode="shift", date_offset_days=0)


@pytest.mark.parametrize("timezone", ["+1460", "+1500", "-1300", "0500"])
def test_shift_rejects_invalid_standalone_timezone(timezone):
    dataset = minimal_dataset()
    dataset.TimezoneOffsetFromUTC = timezone
    with pytest.raises(PolicyError, match="timezone offset"):
        apply(dataset, date_mode="shift", date_offset_days=1)


@pytest.mark.parametrize("pseudonym", ["", " ", "SYNTHETIC^NAME", "SYNTHETIC\\ID", "SYNTHETIC\tID"])
def test_invalid_pseudonym_cannot_make_identifying_or_malformed_values(pseudonym):
    with pytest.raises(PolicyError, match="neutral identifier"):
        scrub_metadata(minimal_dataset(), pseudonym=pseudonym, uid_mapping=MAPPING)


def test_multi_valued_uids_share_one_mapping():
    dataset = minimal_dataset()
    dataset.IrradiationEventUID = [ORIGINAL, REFERENCED, ORIGINAL]
    apply(dataset)
    assert list(dataset.IrradiationEventUID) == ["2.25.101", "2.25.102", "2.25.101"]


def test_unmapped_entity_and_unknown_uid_meaning_fail_closed():
    dataset = minimal_dataset()
    dataset.ReferencedSOPInstanceUID = "2.25.999"
    with pytest.raises(PolicyError, match="batch mapping"):
        apply(dataset)
    dataset = minimal_dataset()
    dataset.SelectorUIValue = ORIGINAL
    with pytest.raises(PolicyError, match="unsupported UID meaning"):
        apply(dataset)


def test_private_fd_is_deleted_before_lazy_conversion():
    dataset = minimal_dataset()
    child = Dataset()
    private_tag = Tag(0x01F1, 0x1026)
    child.add(RawDataElement(private_tag, "FD", 6, b"0.938 ", 0, False, True))
    child.ReferencedSOPInstanceUID = REFERENCED
    dataset.ReferencedImageSequence = Sequence([child])
    apply(dataset)
    assert private_tag not in child
    assert child.ReferencedSOPInstanceUID == "2.25.102"


def test_provenance_graphics_and_icons_removed_even_with_historical_uids():
    dataset = minimal_dataset()
    historical = Dataset()
    historical.ReferencedSOPInstanceUID = "2.25.987654321"
    historical.PatientName = "Synthetic^History"
    dataset.OriginalAttributesSequence = Sequence([historical])
    dataset.IconImageSequence = Sequence([historical])
    dataset.GraphicAnnotationSequence = Sequence([historical])
    dataset.add_new((0x6000, 0x3000), "OW", b"synthetic overlay")
    apply(dataset)
    assert "OriginalAttributesSequence" not in dataset
    assert "IconImageSequence" not in dataset
    assert "GraphicAnnotationSequence" not in dataset
    assert Tag(0x6000, 0x3000) not in dataset


def test_text_is_neutralized_and_clinical_code_exception_is_explicit():
    dataset = minimal_dataset()
    dataset.AccessionNumber = "SYNTHETIC-ID"
    dataset.BeamName = "SYNTHETIC-BEAM"
    dataset.PatientSex = "F"
    dataset.StudyDescription = "Synthetic study description"
    dataset.RescaleType = "HU"
    code = Dataset()
    code.CodeValue = "T-D0050"
    code.CodingSchemeDesignator = "SRT"
    code.CodeMeaning = "Tissue"
    dataset.AnatomicRegionSequence = Sequence([code])
    apply(dataset, text_policy="strict")
    assert dataset.AccessionNumber == dataset.BeamName == "ANONYMIZED"
    assert dataset.PatientSex == ""
    assert "StudyDescription" not in dataset
    assert dataset.RescaleType == "HU"
    assert code.CodeValue == "T-D0050"
    assert code.CodeMeaning == "Tissue"
    assert not metadata_issues(dataset, pseudonym="SUBJECT-TEST")
    dataset.ReferringPhysicianName = "Synthetic^Residual"
    assert "identifying_text" in metadata_issues(dataset, pseudonym="SUBJECT-TEST")


def test_opaque_public_and_protected_binary_data_fail():
    dataset = minimal_dataset()
    dataset.add_new((0x0008, 0x9998), "UN", b"synthetic opaque")
    with pytest.raises(PolicyError, match="unknown-value"):
        apply(dataset)
    dataset = minimal_dataset()
    dataset.EncapsulatedDocument = b"synthetic document"
    with pytest.raises(PolicyError, match="opaque identifying"):
        apply(dataset)


@pytest.mark.parametrize("disguised", ["uid", "sequence"])
def test_reader_rejects_vr_mismatch_that_would_hide_references(tmp_path, disguised):
    dataset = minimal_dataset()
    dataset.file_meta = FileMetaDataset()
    dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset.file_meta.MediaStorageSOPClassUID = dataset.SOPClassUID
    dataset.file_meta.MediaStorageSOPInstanceUID = dataset.SOPInstanceUID
    if disguised == "uid":
        child = Dataset()
        child.add_new((0x0008, 0x1155), "LO", REFERENCED)
        dataset.ReferencedImageSequence = Sequence([child])
    else:
        # Public ReferencedImageSequence encoded as an opaque binary payload must
        # not bypass recursive validation merely because the file still parses.
        dataset.add_new((0x0008, 0x1140), "OB", b"synthetic hidden metadata")
    source = tmp_path / "synthetic-wrong-vr.dcm"
    dcmwrite(source, dataset, enforce_file_format=True)
    with pytest.raises(ReadError) as error:
        read_dicom(source)
    assert REFERENCED not in str(error.value)
    assert "synthetic hidden metadata" not in str(error.value)


def _encapsulated_item(payload: bytes) -> bytes:
    return b"\xfe\xff\x00\xe0" + struct.pack("<I", len(payload)) + payload


@pytest.mark.parametrize("payload", [
    _encapsulated_item(b""),  # Offset table only: no image payload at all.
    _encapsulated_item(b"") + b"\xfe\xff\x00\xe0\x10\x00\x00\x00AB",  # Truncated fragment.
    _encapsulated_item(struct.pack("<I", 16)) + _encapsulated_item(b"ABCD"),  # Offset outside payload.
    _encapsulated_item(b"") + _encapsulated_item(b"ABC"),  # Odd fragment length.
])
def test_compressed_missing_or_invalid_fragments_fail_structure_checks(dicom_factory, payload):
    dataset = dicom_factory()
    dataset.file_meta.TransferSyntaxUID = JPEGBaseline8Bit
    dataset.PixelData = payload
    dataset["PixelData"].is_undefined_length = True
    assert structural_error(dataset) is not None


def test_valid_synthetic_encapsulation_keeps_bytes_and_transfer_syntax(tmp_path, dicom_factory, dicom_writer):
    dataset = dicom_factory()
    dataset.file_meta.TransferSyntaxUID = JPEGBaseline8Bit
    dataset.BitsAllocated = dataset.BitsStored = 8
    dataset.HighBit = 7
    # Structurally valid synthetic fragments, not a codec-decodability test.
    original = _encapsulated_item(struct.pack("<I", 0)) + _encapsulated_item(b"SYNTHETIC-FRAME ")
    dataset.PixelData = original
    dataset["PixelData"].is_undefined_length = True
    source = tmp_path / "source"
    dicom_writer(dataset, source / "synthetic-encapsulated.dcm")
    output = tmp_path / "output"
    report = process_batch(source, output)
    assert report.ok, report.to_dict()
    saved = read_dicom(next(output.glob("*.dcm")))
    assert saved.PixelData == original
    assert saved.file_meta.TransferSyntaxUID == JPEGBaseline8Bit

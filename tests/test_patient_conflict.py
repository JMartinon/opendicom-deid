"""Identity comparison across synthetic CT and RT plan exports."""

import json

import pydicom
import pytest

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.scanner import scan_directory


@pytest.mark.parametrize(("left", "right"), [
    ("Example^Given", "Example^Given^^^"),
    ("Example^^Middle", " Example ^ ^ Middle ^"),
    ("Example^Given=Alt^Given", "Example^Given^^^=Alt^Given^^^=^^^^"),
    ("Example^Given", "^^^^=^^^^"),  # No name information in the second object.
])
def test_dicom_name_formatting_does_not_block_a_plan(
    tmp_path, dicom_factory, dicom_writer, left, right,
):
    image = dicom_factory(patient_name=left)
    plan = dicom_factory("RTPLAN", patient_name=right, study=image.StudyInstanceUID)
    dicom_writer(image, tmp_path / "ct.dcm")
    dicom_writer(plan, tmp_path / "plan.dcm")
    scan = scan_directory(tmp_path)
    assert scan.ok, scan.findings
    assert scan.modalities == {"CT": 1, "RTPLAN": 1}


def test_equivalent_name_encodings_keep_one_pseudonym_in_completed_output(
    tmp_path, dicom_factory, dicom_writer,
):
    source = tmp_path / "source"
    destination = tmp_path / "output"
    image = dicom_factory(patient_name="Example^Given")
    plan = dicom_factory("RTPLAN", patient_name="Example^Given^^^", study=image.StudyInstanceUID)
    dicom_writer(image, source / "ct.dcm")
    dicom_writer(plan, source / "plan.dcm")
    report = process_batch(source, destination)
    assert report.ok and report.output_count == 2, report.summary()
    saved = [pydicom.dcmread(path) for path in destination.rglob("*.dcm")]
    assert {ds.Modality for ds in saved} == {"CT", "RTPLAN"}
    assert len({ds.PatientID for ds in saved}) == 1
    assert all(ds.PatientID != image.PatientID and "Example" not in str(ds.PatientName) for ds in saved)


@pytest.mark.parametrize(("left", "right"), [
    ("Example^Given", "EXAMPLE^Given"),
    ("Exämple^Given", "Example^Given"),
    ("Example^^Middle", "Example^Middle"),
    ("Example=Alias", "Example==Alias"),
    ("Example^Given One", "Example^GivenOne"),
    ("Example^Given", "Example^Given^M"),
])
def test_significant_name_difference_still_blocks_publication(
    tmp_path, dicom_factory, dicom_writer, left, right,
):
    source = tmp_path / "source"
    image = dicom_factory(patient_name=left)
    plan = dicom_factory("RTPLAN", patient_name=right, study=image.StudyInstanceUID)
    image.SpecificCharacterSet = plan.SpecificCharacterSet = "ISO_IR 192"
    dicom_writer(image, source / "ct.dcm")
    dicom_writer(plan, source / "plan.dcm")
    destination = tmp_path / "output"
    report = process_batch(source, destination)
    assert report.status == "FAILED" and report.output_count == 0 and not destination.exists()
    assert any(f.severity == "FAIL" and f.code == "PATIENT_CONFLICT" for f in report.findings)


def test_conflict_identifies_which_files_supplied_each_field_without_disclosing_values(
    tmp_path, dicom_factory, dicom_writer,
):
    source = tmp_path / "private-source-folder"
    image = dicom_factory(patient_name="OriginalSynthetic^Person")
    image.PatientBirthDate = ""
    mr = dicom_factory("MR", patient_name="", study=image.StudyInstanceUID)
    plan = dicom_factory("RTPLAN", patient_name="OtherSynthetic^Person", study=image.StudyInstanceUID)
    plan.PatientBirthDate = "19701203"
    for filename, ds in [("a-private-ct.dcm", image), ("b-private-mr.dcm", mr), ("c-private-plan.dcm", plan)]:
        dicom_writer(ds, source / filename)

    destination = tmp_path / "output"
    report = process_batch(source, destination)
    assert report.status == "FAILED" and report.output_count == 0 and not destination.exists()
    conflicts = [f for f in report.findings if f.code == "PATIENT_CONFLICT" and f.severity == "FAIL"]
    assert {f.file_id for f in conflicts} == {"File 000003"}
    for keyword, tag, origin in [
        ("PatientName", "0010,0010", "File 000001"),
        ("PatientBirthDate", "0010,0030", "File 000002"),
    ]:
        assert any((keyword in f.message or tag in f.message) and origin in f.message for f in conflicts)

    public = json.dumps(report.to_dict()) + report.summary()
    for sensitive in (source.name, "a-private-ct.dcm", "b-private-mr.dcm", "c-private-plan.dcm",
                      image.PatientID, "OriginalSynthetic", "OtherSynthetic", mr.PatientBirthDate,
                      plan.PatientBirthDate, image.StudyInstanceUID, plan.SOPInstanceUID):
        assert sensitive not in public


def test_same_name_with_different_birth_dates_still_blocks_the_batch(
    tmp_path, dicom_factory, dicom_writer,
):
    source = tmp_path / "source"
    image = dicom_factory(patient_name="Example^Given")
    plan = dicom_factory("RTPLAN", patient_name="Example^Given^^^", study=image.StudyInstanceUID)
    plan.PatientBirthDate = "19701203"
    dicom_writer(image, source / "ct.dcm")
    dicom_writer(plan, source / "plan.dcm")
    destination = tmp_path / "output"
    report = process_batch(source, destination)
    assert report.status == "FAILED" and report.output_count == 0 and not destination.exists()
    conflicts = [f for f in report.findings if f.code == "PATIENT_CONFLICT"]
    assert len(conflicts) == 1
    assert "PatientBirthDate" in conflicts[0].message or "0010,0030" in conflicts[0].message

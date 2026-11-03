"""Identity comparison across synthetic CT and RT plan exports."""

import json

import pydicom
import pytest
from conftest import reference

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.scanner import scan_directory


@pytest.mark.parametrize(("left", "right"), [
    ("Example^Given", "Example^Given^^^"),
    ("Example^^Middle", " Example ^ ^ Middle ^"),
    ("Example^Given=Alt^Given", "Example^Given^^^=Alt^Given^^^=^^^^"),
    ("Example^Given", "^^^^=^^^^"),  # No name information in the second object.
    ("Exémple^Given", "EXÉMPLE^GIVEN"),
])
def test_dicom_name_formatting_does_not_block_a_plan(
    tmp_path, dicom_factory, dicom_writer, left, right,
):
    image = dicom_factory(patient_name=left)
    plan = dicom_factory("RTPLAN", patient_name=right, study=image.StudyInstanceUID)
    image.SpecificCharacterSet = plan.SpecificCharacterSet = "ISO_IR 192"
    dicom_writer(image, tmp_path / "ct.dcm")
    dicom_writer(plan, tmp_path / "plan.dcm")
    scan = scan_directory(tmp_path)
    assert scan.ok, scan.findings
    assert scan.modalities == {"CT": 1, "RTPLAN": 1}


def test_case_only_ct_and_plan_keep_one_patient_and_referenced_structures(
    tmp_path, dicom_factory, dicom_writer,
):
    source = tmp_path / "source"
    destination = tmp_path / "output"
    image = dicom_factory(patient_name="example^given")
    struct = dicom_factory("RTSTRUCT", patient_name="Example^Given", study=image.StudyInstanceUID)
    plan = dicom_factory("RTPLAN", patient_name="EXAMPLE^GIVEN^^^", study=image.StudyInstanceUID)
    struct.ReferencedImageSequence = [reference(image)]
    plan.ReferencedStructureSetSequence = [reference(struct)]
    dicom_writer(image, source / "ct.dcm")
    dicom_writer(plan, source / "plan.dcm")
    dicom_writer(struct, source / "struct.dcm")
    report = process_batch(source, destination)
    assert report.ok and report.output_count == 3, report.summary()
    saved = {ds.Modality: ds for ds in (pydicom.dcmread(path) for path in destination.rglob("*.dcm"))}
    assert set(saved) == {"CT", "RTSTRUCT", "RTPLAN"}
    assert len({(ds.PatientID, str(ds.PatientName)) for ds in saved.values()}) == 1
    assert saved["CT"].PatientID != image.PatientID
    assert "example" not in str(saved["CT"].PatientName).lower()
    for referrer, sequence, target in [
        ("RTSTRUCT", "ReferencedImageSequence", "CT"),
        ("RTPLAN", "ReferencedStructureSetSequence", "RTSTRUCT"),
    ]:
        assert saved[referrer][sequence][0].ReferencedSOPInstanceUID == saved[target].SOPInstanceUID
    assert saved["CT"].SOPInstanceUID != image.SOPInstanceUID
    assert saved["RTSTRUCT"].SOPInstanceUID != struct.SOPInstanceUID


@pytest.mark.parametrize(("left", "right"), [
    ("Example^Given", "Sample^Given"),
    ("Weiß^Given", "WEISS^GIVEN"),  # A one-to-many substitution is not solely capitalization.
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
    plan = dicom_factory("RTPLAN", patient_name="EXAMPLE^GIVEN^^^", study=image.StudyInstanceUID)
    plan.PatientBirthDate = "19701203"
    dicom_writer(image, source / "ct.dcm")
    dicom_writer(plan, source / "plan.dcm")
    destination = tmp_path / "output"
    report = process_batch(source, destination)
    assert report.status == "FAILED" and report.output_count == 0 and not destination.exists()
    conflicts = [f for f in report.findings if f.code == "PATIENT_CONFLICT"]
    assert len(conflicts) == 1
    assert "PatientBirthDate" in conflicts[0].message or "0010,0030" in conflicts[0].message


def test_case_only_names_without_patient_id_still_require_review(
    tmp_path, dicom_factory, dicom_writer,
):
    image = dicom_factory(patient_id="", patient_name="Example^Given")
    plan = dicom_factory("RTPLAN", patient_id="", patient_name="EXAMPLE^GIVEN", study=image.StudyInstanceUID)
    dicom_writer(image, tmp_path / "ct.dcm")
    dicom_writer(plan, tmp_path / "plan.dcm")
    scan = scan_directory(tmp_path)
    assert not scan.ok
    assert any(f.code == "PATIENT_CONFLICT" and f.severity == "FAIL" for f in scan.findings)


@pytest.mark.parametrize(("other_id", "other_issuer"), [
    ("example-id", "SYNTHETIC-ISSUER"),  # Case tolerance is limited to PatientName.
    ("EXAMPLE-ID", "OTHER-ISSUER"),
])
def test_case_only_names_do_not_link_distinct_patient_keys(
    tmp_path, dicom_factory, dicom_writer, other_id, other_issuer,
):
    image = dicom_factory(patient_id="EXAMPLE-ID", patient_name="Example^Given")
    plan = dicom_factory("RTPLAN", patient_id=other_id, patient_name="EXAMPLE^GIVEN")
    image.IssuerOfPatientID = "SYNTHETIC-ISSUER"
    plan.IssuerOfPatientID = other_issuer
    dicom_writer(image, tmp_path / "source" / "ct.dcm")
    dicom_writer(plan, tmp_path / "source" / "plan.dcm")
    output = tmp_path / "output"
    report = process_batch(tmp_path / "source", output)
    assert report.ok and report.output_count == 2, report.summary()
    assert len({pydicom.dcmread(path).PatientID for path in output.rglob("*.dcm")}) == 2

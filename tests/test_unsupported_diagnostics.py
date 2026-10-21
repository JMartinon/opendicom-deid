"""Unsupported-object diagnostics identify formats without exposing source identities."""

import json
import warnings

import pytest
from pydicom.dataset import Dataset
from pydicom.uid import EncapsulatedPDFStorage, ExplicitVRLittleEndian

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.sop_classes import unsupported_object_message
from opendicom_deid.core.validator import validate_directory


def test_standard_unsupported_class_is_identified_in_scan_and_validation(
    tmp_path, dicom_factory, dicom_writer,
):
    ds = dicom_factory(pixels=False)
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID = EncapsulatedPDFStorage
    ds.Modality = "DOC"
    source, output = tmp_path / "source", tmp_path / "output"
    dicom_writer(ds, source / "synthetic.dcm")

    scan = scan_directory(source)
    report = process_batch(source, output)
    independent = validate_directory(source)
    assert not scan.ok
    assert not report.ok
    assert not independent.ok
    assert not output.exists()
    for findings in (scan.findings, report.findings, independent.findings):
        failure, = [finding for finding in findings if finding.code == "UNSUPPORTED_OBJECT"]
        assert failure.severity == "FAIL"
        assert EncapsulatedPDFStorage.name in failure.message
        assert str(EncapsulatedPDFStorage) in failure.message
        assert "modality: DOC" in failure.message
        assert "requires dedicated de-identification support" in failure.message


@pytest.mark.parametrize("sop_class", [
    "1.2.826.0.1.3680043.10.999.19600102.12345678",  # Synthetic private class.
    "1.2.840.10008.999.19600102.12345678",  # Unknown even under the standard root.
])
@pytest.mark.parametrize("modality, label", [
    ("RTPLAN", "RTPLAN"),
    ("SYNTHETIC_SECRET", "Other modality"),
    (None, "Unspecified"),
])
def test_unknown_class_and_modality_values_never_enter_public_diagnostics(
    tmp_path, dicom_factory, dicom_writer, sop_class, modality, label, capsys,
):
    ds = dicom_factory(pixels=False, patient_name="Synthetic^Confidential")
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID = sop_class
    if modality is None:
        del ds.Modality
    else:
        ds.Modality = modality
    source, output = tmp_path / "source", tmp_path / "output"
    filename = "Synthetic-Confidential-Patient.dcm"
    dicom_writer(ds, source / filename)

    scan = scan_directory(source)
    report = process_batch(source, output)
    independent = validate_directory(source)
    assert not scan.ok
    assert not report.ok
    assert not independent.ok
    assert not output.exists()
    for findings in (scan.findings, report.findings, independent.findings):
        failure, = [finding for finding in findings if finding.code == "UNSUPPORTED_OBJECT"]
        assert failure.severity == "FAIL"
        assert "unrecognized or private DICOM SOP class" in failure.message
        assert f"modality: {label}" in failure.message
        assert "Check SOP Class UID (0008,0016) locally" in failure.message
    exposed = (
        repr(scan.findings) + json.dumps(report.to_dict()) + report.summary()
        + json.dumps(independent.to_dict()) + independent.summary()
        + capsys.readouterr().err
    )
    for sensitive in (sop_class, "SYNTHETIC_SECRET", str(ds.PatientName), filename):
        assert sensitive not in exposed


@pytest.mark.parametrize("sop_class", ["Synthetic^Confidential", ExplicitVRLittleEndian])
def test_diagnostic_only_identifies_dictionary_sop_classes(sop_class):
    ds = Dataset()
    # A direct helper call also remains safe with malformed values. The strict
    # reader rejects such values before normal scan/validation diagnostics.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        ds.SOPClassUID = sop_class
    with warnings.catch_warnings(record=True) as emitted:
        message = unsupported_object_message(ds)
    assert not emitted
    assert str(sop_class) not in message
    assert "unrecognized or private" in message

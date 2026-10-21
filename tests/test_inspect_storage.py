"""Local header inspection reports storage formats, never patient or raw parser data."""

import importlib.util
import json
import logging
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian

from opendicom_deid.core.scanner import scan_directory

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_storage.py"


@pytest.fixture
def inspector():
    spec = importlib.util.spec_from_file_location("inspect_storage_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_header_inspection_limits_reads_and_output(
    tmp_path, dicom_factory, dicom_writer, inspector, monkeypatch, capsys,
):
    ds = dicom_factory()
    filename = "Synthetic-Confidential-Patient.dcm"
    dicom_writer(ds, tmp_path / filename)
    original_reader = inspector.pydicom.dcmread
    reads = []

    def read(*args, **kwargs):
        assert kwargs["stop_before_pixels"] is True
        assert kwargs["specific_tags"] == ["SOPClassUID", "Modality"]
        dataset = original_reader(*args, **kwargs)
        assert "PixelData" not in dataset
        assert "PatientName" not in dataset
        assert "SOPInstanceUID" not in dataset
        reads.append(dataset)
        return dataset

    monkeypatch.setattr(inspector.pydicom, "dcmread", read)
    assert inspector.main([str(tmp_path), "--file-id", "1"]) == 0
    exposed = capsys.readouterr()
    assert not exposed.err
    result = json.loads(exposed.out)["files"][0]
    assert len(reads) == 1
    assert result["file_id"] == "File 000001"
    assert result["sop_class"] == {"name": CTImageStorage.name, "uid": CTImageStorage}
    assert result["modality"] == "CT"
    assert result["file_meta"]["transfer_syntax"]["uid"] == ExplicitVRLittleEndian
    assert result["supported_storage_class"] is True
    for value in (filename, str(ds.PatientName), str(ds.PatientID), str(ds.SOPInstanceUID),
                  str(ds.StudyInstanceUID), str(ds.SeriesInstanceUID)):
        assert value not in exposed.out


def test_file_number_matches_scanner_including_non_dicom_entries(
    tmp_path, dicom_factory, dicom_writer, inspector, capsys,
):
    (tmp_path / "a-synthetic-note.txt").write_text("Synthetic patient details")
    dicom_writer(dicom_factory("MR"), tmp_path / "b.dcm")
    dicom_writer(dicom_factory("CT"), tmp_path / "nested" / "c.dcm")
    scan = scan_directory(tmp_path)
    assert [record.file_id for record in scan.files] == ["File 000002", "File 000003"]
    assert inspector.main([str(tmp_path), "--file-id", "3"]) == 0
    result = json.loads(capsys.readouterr().out)["files"]
    assert len(result) == 1
    assert result[0]["file_id"] == "File 000003"
    assert result[0]["modality"] == "CT"


@pytest.mark.parametrize("reveal", [False, True])
def test_private_class_identifiers_require_explicit_opt_in(
    tmp_path, dicom_factory, dicom_writer, inspector, capsys, reveal,
):
    private_class = "1.2.826.0.1.3680043.10.999.19600102.12345678"
    ds = dicom_factory()
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID = private_class
    ds.Modality = "SYNTHETIC_SECRET"
    dicom_writer(ds, tmp_path / "synthetic.dcm")
    args = [str(tmp_path), "--file-id", "1"]
    if reveal:
        args.append("--show-private-class-uids")
    assert inspector.main(args) == 0
    exposed = capsys.readouterr()
    result = json.loads(exposed.out)["files"][0]
    assert result["sop_class"]["uid"] == (private_class if reveal else "REDACTED")
    assert result["file_meta"]["media_storage_sop_class"]["uid"] == (
        private_class if reveal else "REDACTED"
    )
    assert result["modality"] == "Other modality"
    assert "SYNTHETIC_SECRET" not in exposed.out + exposed.err
    assert (private_class in exposed.out) is reveal


def test_malformed_uid_is_not_echoed_even_with_private_option(
    tmp_path, dicom_factory, dicom_writer, inspector, capsys,
):
    ds = dicom_factory()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID = "Synthetic^Secret"
        dicom_writer(ds, tmp_path / "secret-filename.dcm")
    assert inspector.main([str(tmp_path), "--file-id", "1", "--show-private-class-uids"]) == 1
    exposed = capsys.readouterr()
    assert json.loads(exposed.out)["files"][0]["status"] == "HEADER_READ_FAILED"
    assert not exposed.err
    assert "Synthetic^Secret" not in exposed.out
    assert "secret-filename" not in exposed.out


@pytest.mark.parametrize("diagnostic", ["warning", "log", "exception"])
def test_parser_diagnostics_never_escape(
    tmp_path, dicom_factory, dicom_writer, inspector, monkeypatch, capsys, diagnostic,
):
    ds = dicom_factory()
    path = dicom_writer(ds, tmp_path / "synthetic.dcm")

    def read(*args, **kwargs):
        if diagnostic == "exception":
            raise ValueError("Synthetic^Confidential")
        if diagnostic == "warning":
            warnings.warn("Synthetic^Confidential", UserWarning)
        else:
            logging.getLogger("pydicom").warning("Synthetic^Confidential")
        return ds

    monkeypatch.setattr(inspector.pydicom, "dcmread", read)
    result = inspector.inspect_header(path, 1, show_private_class_uids=True)
    assert result["status"] == "HEADER_READ_FAILED"
    captured = capsys.readouterr()
    assert "Synthetic^Confidential" not in json.dumps(result) + captured.out + captured.err


def test_private_option_cannot_expose_invalid_uid_or_unknown_transfer_syntax(inspector):
    for value in ("Synthetic^Confidential", "01.2.3", "1." + "2" * 64, ["1.2.3", "1.2.4"]):
        result = inspector._uid_description(value, "SOP Class", True)
        assert result["uid"] in {"REDACTED", None}
    assert inspector._uid_description("1.2.3.4.5", "Transfer Syntax", True)["uid"] == "REDACTED"


def test_script_runs_directly_without_installed_checkout(tmp_path, dicom_factory, dicom_writer):
    dicom_writer(dicom_factory(), tmp_path / "synthetic.dcm")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path), "--file-id", "1"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert not result.stderr
    assert json.loads(result.stdout)["files"][0]["sop_class"]["uid"] == CTImageStorage


@pytest.mark.parametrize("number", ["0", "2"])
def test_invalid_inventory_number_is_a_safe_error(
    tmp_path, dicom_factory, dicom_writer, inspector, capsys, number,
):
    dicom_writer(dicom_factory(), tmp_path / "synthetic-confidential.dcm")
    assert inspector.main([str(tmp_path), "--file-id", number]) == 2
    exposed = capsys.readouterr()
    assert json.loads(exposed.out)["status"] == "FAILED"
    assert "synthetic-confidential" not in exposed.out + exposed.err

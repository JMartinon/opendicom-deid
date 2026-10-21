"""Privacy boundaries and changes to input while a job is running."""

import os
import subprocess
import sys
from pathlib import Path

import pydicom
import pytest

from opendicom_deid.core.anonymizer import process_batch


def test_engine_import_and_processing_work_with_network_blocked(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "input"
    dicom_writer(dicom_factory(), source / "synthetic.dcm")
    code = """
import socket
import sys
def denied(*args, **kwargs):
    raise AssertionError('Application attempted network access')
socket.socket.connect = denied
socket.create_connection = denied
from opendicom_deid.core.anonymizer import process_batch
result = process_batch(sys.argv[1], sys.argv[2])
assert result.ok, result.summary()
print('offline processing passed')
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", code, str(source), str(tmp_path / "output")],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "offline processing passed"


@pytest.mark.parametrize("change", ["add", "edit", "remove"])
def test_changes_to_source_prevent_publication(tmp_path, dicom_factory, dicom_writer, change):
    source, output = tmp_path / "input", tmp_path / "output"
    path = dicom_writer(dicom_factory(), source / "one.dcm")

    def progress(event):
        if event.phase != "De-identifying":
            return
        if change == "add":
            dicom_writer(dicom_factory(), source / "two.dcm")
        elif change == "edit":
            path.write_bytes(path.read_bytes() + b"CHANGED")
        else:
            path.unlink()

    report = process_batch(source, output, progress=progress)
    assert report.status == "FAILED"
    assert not report.ok
    assert not output.exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))


def test_patient_id_is_scoped_by_issuer(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "input"
    for index, issuer in enumerate(("SYNTHETIC-A", "SYNTHETIC-B")):
        ds = dicom_factory(patient_id="SAME-SYNTHETIC-ID")
        ds.IssuerOfPatientID = issuer
        dicom_writer(ds, source / f"{index}.dcm")
    report = process_batch(source, tmp_path / "output")
    assert report.ok, report.summary()
    patients = {str(pydicom.dcmread(path).PatientID) for path in (tmp_path / "output").rglob("*.dcm")}
    assert len(patients) == 2


def test_report_never_overwrites_an_existing_file(tmp_path):
    from opendicom_deid.core.report import Report

    target = tmp_path / "existing.json"
    target.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        Report().save(target)
    assert target.read_text(encoding="utf-8") == "original"

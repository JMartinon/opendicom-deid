"""Synthetic source trees retain their useful structure without copying path identities."""

import shutil
from pathlib import Path
from threading import Event

import pydicom
import pytest

from opendicom_deid.cli import parser
from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.models import Options


def test_default_keeps_nested_patients_studies_and_repeated_folder_names(
    tmp_path, dicom_factory, dicom_writer,
):
    source, output = tmp_path / "input", tmp_path / "output"
    locations = {
        1: "SYNTHETIC-ALICE/FirstStudy/Images",
        2: "SYNTHETIC-ALICE/FirstStudy/Images",
        3: "SYNTHETIC-ALICE/FollowUp/Images",
        4: "SYNTHETIC-BOB/FirstStudy/Images",
        5: "SYNTHETIC-BOB/FirstStudy/Plans",
    }
    for number, folder in locations.items():
        ds = dicom_factory(patient_id="SYNTHETIC-A" if number < 4 else "SYNTHETIC-B")
        ds.InstanceNumber = number
        dicom_writer(ds, source / folder / f"PRIVATE-NAME-{number}.dcm")

    report = process_batch(source, output)
    assert report.ok, report.summary()
    results = {
        int(pydicom.dcmread(path).InstanceNumber): path.relative_to(output)
        for path in output.rglob("*.dcm")
    }
    assert len(results) == len(locations)
    # Existing separate patient folders need no additional patient wrapper.
    assert {len(path.parts) for path in results.values()} == {4}
    assert results[1].parent == results[2].parent
    assert results[1].parent != results[3].parent
    assert results[1].parents[2] == results[3].parents[2]
    assert results[1].parents[2] != results[4].parents[2]
    assert results[4].parents[1] == results[5].parents[1]
    assert results[4].parent != results[5].parent
    assert all(part.startswith("group-") for path in results.values() for part in path.parts[:-1])
    for path in results.values():
        assert path.name.startswith("2.25.")
        for original in ("SYNTHETIC", "FirstStudy", "Images", "FollowUp", "Plans", "PRIVATE"):
            assert original not in str(path)


@pytest.mark.parametrize("relative_parent", [Path("."), Path("SYNTHETIC-CLINIC/Study")])
def test_patients_sharing_a_directory_get_separate_pseudonym_folders(
    tmp_path, dicom_factory, dicom_writer, relative_parent,
):
    source, output = tmp_path / "input", tmp_path / "output"
    for number, identity in enumerate(("SYNTHETIC-A", "SYNTHETIC-A", "SYNTHETIC-B"), 1):
        ds = dicom_factory(patient_id=identity)
        ds.InstanceNumber = number
        dicom_writer(ds, source / relative_parent / f"PRIVATE-{number}.dcm")

    report = process_batch(source, output)
    assert report.ok, report.summary()
    results = {}
    for path in output.rglob("*.dcm"):
        ds = pydicom.dcmread(path)
        results[int(ds.InstanceNumber)] = path
        assert path.parent.name == ds.PatientID
        assert len(path.relative_to(output).parts) == len(relative_parent.parts) + 2
        assert "SYNTHETIC" not in str(path.relative_to(output))
        assert "PRIVATE" not in str(path.relative_to(output))
    assert len(results) == 3
    assert results[1].parent == results[2].parent
    assert results[1].parent != results[3].parent
    assert results[1].parent.parent == results[3].parent.parent


def test_duplicate_copies_keep_their_nested_locations_and_shared_instance_uid(
    tmp_path, dicom_factory, dicom_writer,
):
    source, output = tmp_path / "input", tmp_path / "output"
    original = dicom_writer(dicom_factory(), source / "PRIVATE-PATIENT/Study/Original/image.dcm")
    copied = source / "PRIVATE-PATIENT/Study/Copies/image.dcm"
    copied.parent.mkdir()
    shutil.copyfile(original, copied)

    report = process_batch(source, output)
    assert report.ok, report.summary()
    files = list(output.rglob("*.dcm"))
    assert len(files) == 2
    assert files[0].parent != files[1].parent
    assert files[0].parents[1] == files[1].parents[1]
    assert pydicom.dcmread(files[0]).SOPInstanceUID == pydicom.dcmread(files[1]).SOPInstanceUID
    assert any(f.code == "DUPLICATE_COPY" for f in report.findings)


def test_flat_organization_remains_an_explicit_option(tmp_path, dicom_factory, dicom_writer):
    source, output = tmp_path / "input", tmp_path / "output"
    for number, patient in enumerate(("SYNTHETIC-A", "SYNTHETIC-B")):
        dicom_writer(dicom_factory(patient_id=patient), source / patient / "study" / f"{number}.dcm")

    report = process_batch(source, output, Options(organization="flat"))
    assert report.ok, report.summary()
    assert len(list(output.glob("*.dcm"))) == 2
    assert not any(path.is_dir() for path in output.iterdir())


def test_cli_preserves_source_structure_unless_flat_is_requested():
    command = parser()
    assert command.parse_args(["deidentify", "input", "output"]).organization == "source"
    assert command.parse_args(
        ["deidentify", "input", "output", "--organization", "flat"]
    ).organization == "flat"


@pytest.mark.parametrize("interruption", ["cancel", "write_failure"])
def test_partial_nested_output_is_discarded(
    tmp_path, dicom_factory, dicom_writer, monkeypatch, interruption,
):
    from opendicom_deid.core import anonymizer

    source, output = tmp_path / "input", tmp_path / "output"
    output.mkdir()
    for number, patient in enumerate(("SYNTHETIC-A", "SYNTHETIC-B")):
        dicom_writer(dicom_factory(patient_id=patient), source / "Study/Images" / f"{number}.dcm")
    original_write = anonymizer._write
    cancel = Event()

    def interrupted_write(ds, target):
        original_write(ds, target)
        if interruption == "cancel":
            cancel.set()
        else:
            raise OSError("Synthetic write failure")

    monkeypatch.setattr(anonymizer, "_write", interrupted_write)
    report = process_batch(source, output, cancel=cancel)
    assert report.status == ("CANCELLED" if interruption == "cancel" else "FAILED")
    assert not list(output.iterdir())
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))
    assert len(list(source.rglob("*.dcm"))) == 2

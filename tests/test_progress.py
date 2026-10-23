"""Progress reflects completed work, including failures and final publication checks."""

from threading import Event

import pytest
from pydicom.uid import EncapsulatedPDFStorage

from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.models import Progress
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.gui.progress import LatestProgress, progress_display


@pytest.mark.parametrize("phase", ["Scanning", "De-identifying", "Validating", "Rechecking source files"])
def test_stage_percentage_is_file_based_and_does_not_round_up(phase):
    display = progress_display(Progress(phase, 650, 651))
    assert 99 < display.percentage < 100
    assert "650 / 651 files" in display.label
    assert "99% of this stage" in display.label
    assert progress_display(Progress(phase, 651, 651)).percentage == 100


@pytest.mark.parametrize("phase", ["Inventory", "Checking references", "Publishing", "Rechecking source files"])
def test_unknown_length_stages_are_indeterminate(phase):
    display = progress_display(Progress(phase, 0, 0))
    assert display.percentage is None
    assert "0 / 0" not in display.label and "%" not in display.label


def test_progress_backlog_keeps_only_current_state():
    latest = LatestProgress()
    latest.begin()
    for index in range(100_000):
        latest.publish(Progress("Scanning", index, 100_000))
    assert latest.take() == Progress("Scanning", 99_999, 100_000)
    assert latest.take() is None
    latest.publish(Progress("Validating", 1, 2))
    assert latest.take() == Progress("Validating", 1, 2)


def test_completion_discards_progress_that_arrived_after_the_ui_read():
    latest = LatestProgress()
    latest.begin()
    latest.publish(Progress("Validating", 4, 4))
    assert latest.take() == Progress("Validating", 4, 4)
    # Worker publication can race between the UI's read and its completion poll.
    latest.publish(Progress("Publishing", 0, 0))
    latest.finish()
    assert latest.take() is None
    latest.publish(Progress("Publishing", 0, 0))
    assert latest.take() is None
    latest.begin()
    latest.publish(Progress("Scanning", 0, 10))
    assert latest.take() == Progress("Scanning", 0, 10)


def test_scan_progress_counts_skipped_and_rejected_files(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "input"
    dicom_writer(dicom_factory(), source / "a.dcm")
    (source / "b.txt").write_text("Synthetic non-DICOM", encoding="utf-8")
    unsupported = dicom_factory()
    unsupported.SOPClassUID = unsupported.file_meta.MediaStorageSOPClassUID = EncapsulatedPDFStorage
    unsupported.Modality = "DOC"
    dicom_writer(unsupported, source / "c.dcm")
    events = []
    result = scan_directory(source, progress=events.append)
    assert not result.ok and result.total == 1 and result.skipped == 1
    assert events[0] == Progress("Inventory", 0, 0)
    assert [event.completed for event in events if event.phase == "Scanning"] == [0, 1, 2, 3]
    assert all(event.total == 3 for event in events if event.phase == "Scanning")


def test_processing_progress_includes_final_source_check_before_publication(
    tmp_path, dicom_factory, dicom_writer,
):
    source, output = tmp_path / "input", tmp_path / "output"
    for index in range(3):
        dicom_writer(dicom_factory(), source / f"{index}.dcm")
    events = []

    def progress(event):
        events.append(event)
        assert not output.exists(), "Progress must not claim success before atomic publication."

    report = process_batch(source, output, progress=progress)
    assert report.ok, report.summary()
    for phase in ("Scanning", "De-identifying", "Validating", "Rechecking source files"):
        assert [event.completed for event in events if event.phase == phase and event.total] == [0, 1, 2, 3]
    assert events[-1] == Progress("Publishing", 0, 0)
    assert events.index(Progress("Validating", 3, 3)) < events.index(Progress("Rechecking source files", 0, 0))
    assert len(list(output.rglob("*.dcm"))) == 3


def test_cancel_from_final_check_does_not_publish(tmp_path, dicom_factory, dicom_writer):
    source, output = tmp_path / "input", tmp_path / "output"
    dicom_writer(dicom_factory(), source / "synthetic.dcm")
    cancel = Event()
    events = []

    def progress(event):
        events.append(event)
        if event.phase == "Rechecking source files":
            cancel.set()

    report = process_batch(source, output, cancel=cancel, progress=progress)
    assert report.status == "CANCELLED"
    assert not output.exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))
    assert not any(event.phase == "Publishing" for event in events)

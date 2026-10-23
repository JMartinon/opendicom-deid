"""Synthetic-only tests of process isolation and transactional parallel writes."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Event
from uuid import UUID

import pydicom
import pytest
from conftest import reference

from opendicom_deid.core import anonymizer, parallel, uid_mapper
from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.models import Options
from opendicom_deid.core.parallel import FileTask, WorkerState


def _observed_transform(task, markers):
    """Test-only barrier proves that two spawned processes execute concurrently."""
    marker = markers / str(os.getpid())
    started = time.monotonic()
    marker.write_text(json.dumps([started, None]), encoding="utf-8")
    deadline = started + 15
    while len(list(markers.iterdir())) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(list(markers.iterdir())) == 2
    result = parallel._transform(task)
    marker.write_text(json.dumps([started, time.monotonic()]), encoding="utf-8")
    return result


def _failed_write_transform(task):
    """Inject a real child-side partial write without a production test hook."""
    if task.file_id != "File 000002":
        return parallel._transform(task)
    original = anonymizer._write

    def failed_write(ds, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"SYNTHETIC-PARTIAL")
        raise PermissionError(13, "SYNTHETIC-PRIVATE-ERROR", str(target))

    anonymizer._write = failed_write
    try:
        return parallel._transform(task)
    finally:
        anonymizer._write = original


def _terminated_worker(task):
    os._exit(7)


@pytest.mark.parametrize("workers", [0, -1, 9, True, False, 1.0, "2", None])
def test_workers_reject_invalid_values(workers):
    with pytest.raises(ValueError, match="whole number from 1 to 8"):
        Options(workers=workers)


def test_workers_default_is_sequential():
    assert Options().workers == 1
    assert Options(workers=8).workers == 8


@pytest.mark.parametrize("workers", [1, 2])
def test_single_file_does_not_spawn(tmp_path, dicom_factory, dicom_writer, monkeypatch, workers):
    dicom_writer(dicom_factory(), tmp_path / "input" / "one.dcm")

    def unexpected_pool(*args, **kwargs):
        raise AssertionError("A single file does not require a process pool")

    monkeypatch.setattr(parallel, "ProcessPoolExecutor", unexpected_pool)
    report = process_batch(tmp_path / "input", tmp_path / "output", Options(workers=workers))
    assert report.ok, report.summary()


def test_two_spawned_processes_overlap(tmp_path, dicom_factory, dicom_writer, monkeypatch):
    source, output, markers = tmp_path / "input", tmp_path / "output", tmp_path / "markers"
    markers.mkdir()
    for index in range(2):
        dicom_writer(dicom_factory(), source / f"{index}.dcm")
    executor_type = parallel.ProcessPoolExecutor
    pool_records = []

    def observed_executor(**kwargs):
        assert kwargs["mp_context"].get_start_method() == "spawn"
        pool = executor_type(**kwargs)
        submit = pool.submit
        shutdown = pool.shutdown
        pool_records.append({"maximum": kwargs["max_workers"], "joined": False})

        def observed_submit(function, task):
            assert function is parallel._transform
            return submit(_observed_transform, task, markers)

        def observed_shutdown(**options):
            assert options == {"wait": True, "cancel_futures": True}
            shutdown(**options)
            pool_records[-1]["joined"] = True

        pool.submit = observed_submit
        pool.shutdown = observed_shutdown
        return pool

    monkeypatch.setattr(parallel, "ProcessPoolExecutor", observed_executor)
    report = process_batch(source, output, Options(workers=2))
    assert report.ok, report.summary()
    intervals = [json.loads(path.read_text()) for path in markers.iterdir()]
    assert len(intervals) == 2
    assert all(int(path.name) != os.getpid() for path in markers.iterdir())
    assert max(start for start, _ in intervals) < min(end for _, end in intervals)
    assert pool_records == [{"maximum": 2, "joined": True}]


def test_parallel_matches_sequential_bytes_and_batch_links(
    tmp_path, dicom_factory, dicom_writer, monkeypatch
):
    source = tmp_path / "input"
    image = dicom_factory()
    other = dicom_factory(patient_id="CROSS-SECRET", patient_name="Other^Synthetic")
    struct = dicom_factory("RTSTRUCT", study=image.StudyInstanceUID, frame=image.FrameOfReferenceUID)
    struct.ReferencedImageSequence = [reference(image)]
    plan = dicom_factory("RTPLAN", study=image.StudyInstanceUID, frame=image.FrameOfReferenceUID)
    plan.ReferencedStructureSetSequence = [reference(struct)]
    plan.TreatmentMachineName = "CROSS-SECRET"
    dose = dicom_factory("RTDOSE", study=image.StudyInstanceUID, frame=image.FrameOfReferenceUID)
    dose.ReferencedRTPlanSequence = [reference(plan)]
    for index, ds in enumerate((image, other, struct, plan, dose, image)):
        ds.add_new(0x00111010, "LO", "SYNTHETIC-PRIVATE")
        dicom_writer(ds, source / ("nested" if index > 1 else "same-folder") / f"{index}.dcm")

    # Fixed randomness in the parent makes byte-for-byte comparison meaningful;
    # spawned workers receive these mappings and must not generate their own.
    outputs, reports = [], []
    for workers in (1, 2):
        replacement_uids = iter(f"2.25.{value}" for value in range(100, 200))
        pseudonyms = iter(UUID(int=value) for value in range(100, 200))
        monkeypatch.setattr(uid_mapper, "generate_uid", lambda **kwargs: next(replacement_uids))
        monkeypatch.setattr(anonymizer, "uuid4", lambda: next(pseudonyms))
        monkeypatch.setattr(anonymizer.secrets, "choice", lambda values: 1)
        monkeypatch.setattr(anonymizer.secrets, "randbelow", lambda limit: 41)
        output = tmp_path / f"output-{workers}"
        events = []
        report = process_batch(source, output, Options(workers=workers, date_mode="shift"), progress=events.append)
        assert report.ok, report.summary()
        progress = [event.completed for event in events if event.phase == "De-identifying"]
        assert progress == list(range(7))
        outputs.append({str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*.dcm")})
        reports.append(report.to_dict())
    assert outputs[0] == outputs[1]
    assert reports[0] == reports[1]
    saved = [pydicom.dcmread(path) for path in (tmp_path / "output-2").rglob("*.dcm")]
    plan_out = next(ds for ds in saved if ds.Modality == "RTPLAN")
    assert plan_out.TreatmentMachineName != "CROSS-SECRET"
    assert len({str(ds.PatientID) for ds in saved}) == 2
    assert len(saved) == 6


@pytest.mark.parametrize("change", ["edit", "remove", "add"])
def test_source_changes_abort_parallel_transaction(
    tmp_path, dicom_factory, dicom_writer, change
):
    source, output = tmp_path / "input", tmp_path / "output"
    path = dicom_writer(dicom_factory(), source / "one.dcm")
    dicom_writer(dicom_factory(), source / "two.dcm")

    def progress(event):
        if event.phase != "De-identifying" or event.completed != 0:
            return
        if change == "edit":
            path.write_bytes(path.read_bytes() + b"SYNTHETIC-CHANGE")
        elif change == "remove":
            path.unlink()
        else:
            dicom_writer(dicom_factory(), source / "added.dcm")

    report = process_batch(source, output, Options(workers=2), progress=progress)
    assert report.status == "FAILED"
    assert not output.exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))


def test_parallel_cancel_waits_for_writers_before_cleanup(tmp_path, dicom_factory, dicom_writer):
    source, output = tmp_path / "input", tmp_path / "output"
    for index in range(10):
        dicom_writer(dicom_factory(), source / f"{index:02d}.dcm")
    cancel = Event()
    completions = []

    def progress(event):
        if event.phase == "De-identifying" and event.completed:
            completions.append(event.completed)
            cancel.set()

    report = process_batch(source, output, Options(workers=2), cancel=cancel, progress=progress)
    assert report.status == "CANCELLED"
    assert 1 <= max(completions) <= 2
    assert not output.exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))
    # A worker may not recreate a staging directory after cancellation returns.
    time.sleep(0.2)
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))


@pytest.mark.parametrize("worker", [_failed_write_transform, _terminated_worker])
def test_parallel_worker_failures_discard_every_output(
    tmp_path, dicom_factory, dicom_writer, monkeypatch, capfd, worker
):
    source, output = tmp_path / "input", tmp_path / "output"
    for index in range(6):
        dicom_writer(dicom_factory(), source / f"{index}.dcm")
    executor_type = parallel.ProcessPoolExecutor
    submitted = []
    outstanding = set()
    joined = []

    def failing_executor(**kwargs):
        pool = executor_type(**kwargs)
        submit = pool.submit
        shutdown = pool.shutdown

        def altered_submit(function, task):
            # No replay, no bulk submission beyond the configured worker bound.
            outstanding.difference_update(future for future in list(outstanding) if future.done())
            assert len(outstanding) < 2
            submitted.append(task.file_id)
            future = submit(worker, task)
            outstanding.add(future)
            return future

        def joined_shutdown(**options):
            shutdown(**options)
            joined.append(True)

        pool.submit = altered_submit
        pool.shutdown = joined_shutdown
        return pool

    monkeypatch.setattr(parallel, "ProcessPoolExecutor", failing_executor)
    report = process_batch(source, output, Options(workers=2))
    assert report.status == "FAILED"
    assert any(finding.code == "PROCESSING_ERROR" for finding in report.findings)
    assert joined == [True]
    assert len(submitted) == len(set(submitted))
    assert not output.exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))
    assert "SYNTHETIC-PRIVATE-ERROR" not in json.dumps(report.to_dict())
    captured = capfd.readouterr()
    assert "SYNTHETIC-PRIVATE-ERROR" not in captured.err + captured.out
    # Failed workers may not recreate staging after cleanup returns either.
    time.sleep(0.2)
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))


def test_parallel_outputs_still_require_independent_validation(
    tmp_path, dicom_factory, dicom_writer
):
    source, output = tmp_path / "input", tmp_path / "output"
    for index in range(2):
        dicom_writer(dicom_factory(), source / f"{index}.dcm")
    tampered = []

    def progress(event):
        if event.phase != "De-identifying" or event.completed != event.total:
            return
        # Every child write is finished, but publication and validation have
        # not run. Simulate corruption between the two phases.
        staging = next(tmp_path.glob(".opendicom-incomplete-*"))
        path = next(staging.rglob("*.dcm"))
        ds = pydicom.dcmread(path)
        ds.PixelData = b"\0" * len(ds.PixelData)
        dicom_writer(ds, path)
        tampered.append(path)

    report = process_batch(source, output, Options(workers=2), progress=progress)
    assert len(tampered) == 1
    assert report.status == "FAILED"
    assert any(finding.code == "CLINICAL_DATA_CHANGED" for finding in report.findings)
    assert not output.exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))


def test_worker_exception_result_contains_no_sensitive_text(tmp_path, monkeypatch):
    secret = "SYNTHETIC-IDENTIFIER-NOT-FOR-LOGS"
    task = FileTask(tmp_path / secret, tmp_path / "output", "hash", "File 000001", "SUBJECT", 0, {})
    monkeypatch.setattr(parallel, "_state", WorkerState({}, frozenset(), Options(workers=2)))
    monkeypatch.setattr(parallel, "_cancel", Event())

    def denied(path):
        raise PermissionError(13, secret, str(path))

    monkeypatch.setattr(parallel, "file_digest", denied)
    result = parallel._transform(task)
    assert result.finding.code == "PROCESSING_ERROR"
    assert "errno 13" in result.finding.message
    assert secret not in json.dumps(result.finding.__dict__)
    assert secret not in repr(task)


def test_transform_warnings_are_captured_without_values(tmp_path, monkeypatch, capsys, caplog):
    import logging
    import warnings

    secret = "SYNTHETIC-WARNING-SECRET"
    task = FileTask(Path("synthetic-input"), tmp_path / "output", "hash", "File 000001", "SUBJECT", 0, {})
    monkeypatch.setattr(parallel, "_state", WorkerState({}, frozenset(), Options(workers=2)))
    monkeypatch.setattr(parallel, "_cancel", Event())
    monkeypatch.setattr(parallel, "file_digest", lambda path: "hash")
    monkeypatch.setattr(parallel, "read_dicom", lambda path: object())
    monkeypatch.setattr(parallel, "apply_reference_class_repairs", lambda *args: None)
    monkeypatch.setattr(anonymizer, "_reset_file_meta", lambda ds: None)

    def warn(*args, **kwargs):
        warnings.warn(secret, UserWarning)
        logging.getLogger("pydicom").warning(secret)

    monkeypatch.setattr(parallel, "scrub_metadata", warn)
    result = parallel._transform(task)
    assert result.finding.code == "UNSUPPORTED_METADATA"
    assert secret not in json.dumps(result.finding.__dict__)
    assert secret not in capsys.readouterr().err
    assert secret not in caplog.text

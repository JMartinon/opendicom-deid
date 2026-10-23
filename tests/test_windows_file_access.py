"""Synthetic Windows lock injection; these checks do not require a Windows host."""

from __future__ import annotations

import errno
import json
from pathlib import Path
from threading import Event

import pytest

from opendicom_deid.core import anonymizer, filesystem
from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.models import Cancelled, Progress


def windows_error(code: int) -> OSError:
    error = OSError(errno.EACCES, "SYNTHETIC-PRIVATE-DETAIL", "SYNTHETIC-PRIVATE-PATH")
    error.winerror = code
    return error


@pytest.mark.parametrize("code,retry_access_denied", [(32, False), (33, False), (5, True)])
def test_transient_windows_lock_preserves_successful_result(monkeypatch, code, retry_access_denied):
    calls = []
    waits = []
    result = object()

    def operation():
        calls.append(None)
        if len(calls) == 1:
            raise windows_error(code)
        return result

    monkeypatch.setattr(filesystem.time, "sleep", waits.append)
    assert filesystem.retry_windows_file_lock(
        operation, retry_access_denied=retry_access_denied,
    ) is result
    assert len(calls) == 2
    assert len(waits) == 1


@pytest.mark.parametrize("error", [windows_error(5), windows_error(112), PermissionError(errno.EACCES, "denied")])
def test_permanent_or_non_windows_errors_are_not_retried(monkeypatch, error):
    calls = []
    waits = []

    def operation():
        calls.append(None)
        raise error

    monkeypatch.setattr(filesystem.time, "sleep", waits.append)
    with pytest.raises(OSError) as caught:
        filesystem.retry_windows_file_lock(operation)
    assert caught.value is error
    assert len(calls) == 1
    assert waits == []


def test_persistent_windows_lock_has_a_bounded_retry_budget(monkeypatch):
    calls = []
    waits = []
    error = windows_error(32)

    def operation():
        calls.append(None)
        raise error

    monkeypatch.setattr(filesystem.time, "sleep", waits.append)
    with pytest.raises(OSError) as caught:
        filesystem.retry_windows_file_lock(operation)
    assert caught.value is error
    assert 1 < len(calls) <= 6
    assert len(waits) == len(calls) - 1
    assert sum(waits) <= 2


def test_cancellation_interrupts_lock_wait_before_another_attempt(monkeypatch):
    calls = []
    cancel = Event()

    def operation():
        calls.append(None)
        raise windows_error(32)

    def cancelled_wait(timeout):
        assert timeout > 0
        cancel.set()
        return True

    monkeypatch.setattr(cancel, "wait", cancelled_wait)
    with pytest.raises(Cancelled):
        filesystem.retry_windows_file_lock(operation, cancel=cancel)
    assert len(calls) == 1


def create_source(tmp_path, dicom_factory, dicom_writer):
    source = tmp_path / "SYNTHETIC-PRIVATE-INPUT"
    original = dicom_writer(dicom_factory(), source / "one.dcm")
    return source, original, tmp_path / "output"


def test_output_creation_retries_only_before_opening_the_file(
    tmp_path, dicom_factory, dicom_writer, monkeypatch,
):
    source, _, destination = create_source(tmp_path, dicom_factory, dicom_writer)
    original_open = Path.open
    opens = []

    def occasionally_locked(path, mode="r", *args, **kwargs):
        if mode == "xb" and path.suffix == ".dcm":
            opens.append(path)
            if len(opens) == 1:
                raise windows_error(32)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", occasionally_locked)
    monkeypatch.setattr(filesystem.time, "sleep", lambda _: None)
    report = process_batch(source, destination)
    assert report.ok, report.to_dict()
    assert len(opens) == 2
    assert opens[0] == opens[1]
    assert len(list(destination.glob("*.dcm"))) == 1


def test_partial_dicom_write_is_never_replayed(
    tmp_path, dicom_factory, dicom_writer, monkeypatch,
):
    source, _, destination = create_source(tmp_path, dicom_factory, dicom_writer)
    writes = []

    def partial_write(stream, *args, **kwargs):
        writes.append(None)
        stream.write(b"partial synthetic DICOM")
        raise windows_error(32)

    monkeypatch.setattr(anonymizer, "dcmwrite", partial_write)
    monkeypatch.setattr(filesystem.time, "sleep", lambda _: None)
    report = process_batch(source, destination)
    assert not report.ok
    assert len(writes) == 1
    assert not destination.exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))


def test_transient_publication_lock_keeps_the_validated_output(
    tmp_path, dicom_factory, dicom_writer, monkeypatch,
):
    source, original, destination = create_source(tmp_path, dicom_factory, dicom_writer)
    original_bytes = original.read_bytes()
    real_rename = Path.rename
    real_digest = anonymizer.file_digest
    attempts = []
    events = []
    rechecked_files = []

    def progress(event):
        events.append(event)
        assert not destination.exists(), "Progress must precede atomic publication."

    def tracked_digest(path):
        if events and events[-1].phase == "Rechecking source files":
            rechecked_files.append(path)
        return real_digest(path)

    def occasionally_locked(path, target):
        if target == destination:
            attempts.append(None)
            if len(attempts) == 1:
                raise windows_error(32)
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", occasionally_locked)
    monkeypatch.setattr(anonymizer, "file_digest", tracked_digest)
    monkeypatch.setattr(filesystem.time, "sleep", lambda _: None)
    report = process_batch(source, destination, progress=progress)
    assert report.ok, report.to_dict()
    assert len(attempts) == 2
    assert rechecked_files == [original, original]
    first_recheck = events.index(Progress("Rechecking source files", 0, 0))
    assert events.index(Progress("Validating", 1, 1)) < first_recheck
    assert events[first_recheck:] == [
        Progress("Rechecking source files", 0, 0),
        Progress("Rechecking source files", 0, 1),
        Progress("Rechecking source files", 1, 1),
        Progress("Publishing", 0, 0),
    ] * 2
    assert original.read_bytes() == original_bytes
    assert len(list(destination.glob("*.dcm"))) == 1
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))


def test_persistent_publication_lock_is_private_and_discards_staging(
    tmp_path, dicom_factory, dicom_writer, monkeypatch,
):
    source, _, destination = create_source(tmp_path, dicom_factory, dicom_writer)
    real_rename = Path.rename
    attempts = []

    def locked(path, target):
        if target == destination:
            attempts.append(None)
            raise windows_error(32)
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", locked)
    monkeypatch.setattr(filesystem.time, "sleep", lambda _: None)
    report = process_batch(source, destination)
    assert not report.ok
    assert 1 < len(attempts) <= 6
    assert not destination.exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))
    failures = [finding for finding in report.findings if finding.severity == "FAIL"]
    assert failures
    assert all(finding.file_id is None for finding in failures)
    serialized = json.dumps(report.to_dict())
    assert "32" in serialized
    assert "publish" in serialized.lower() or "publication" in serialized.lower()
    assert "SYNTHETIC-PRIVATE" not in serialized
    assert str(tmp_path) not in serialized


def test_transient_source_hash_lock_restarts_final_check_before_publication(
    tmp_path, dicom_factory, dicom_writer, monkeypatch,
):
    source, first, destination = create_source(tmp_path, dicom_factory, dicom_writer)
    second = dicom_writer(dicom_factory(), source / "two.dcm")
    original_bytes = [first.read_bytes(), second.read_bytes()]
    real_digest = anonymizer.file_digest
    real_rename = Path.rename
    events = []
    reads = []
    successful_reads = []
    publications = []

    def progress(event):
        events.append(event)
        assert not destination.exists(), "Progress must precede atomic publication."

    def occasionally_locked(path):
        rechecking = events and events[-1].phase == "Rechecking source files"
        if rechecking:
            reads.append(path)
            if len(reads) == 2:
                assert path == second
                raise windows_error(32)
        digest = real_digest(path)
        if rechecking:
            successful_reads.append(path)
        return digest

    def checked_rename(path, target):
        if target == destination:
            assert successful_reads == [first, first, second]
            publications.append(target)
        return real_rename(path, target)

    monkeypatch.setattr(anonymizer, "file_digest", occasionally_locked)
    monkeypatch.setattr(Path, "rename", checked_rename)
    monkeypatch.setattr(filesystem.time, "sleep", lambda _: None)
    report = process_batch(source, destination, progress=progress)
    assert report.ok, report.to_dict()
    assert reads == [first, second, first, second]
    assert publications == [destination]
    first_recheck = events.index(Progress("Rechecking source files", 0, 0))
    assert events.index(Progress("Validating", 2, 2)) < first_recheck
    complete_attempt = [
        Progress("Rechecking source files", 0, 0),
        Progress("Rechecking source files", 0, 2),
        Progress("Rechecking source files", 1, 2),
        Progress("Rechecking source files", 2, 2),
        Progress("Publishing", 0, 0),
    ]
    assert events[first_recheck:] == complete_attempt[:3] + complete_attempt
    assert [first.read_bytes(), second.read_bytes()] == original_bytes
    assert len(list(destination.glob("*.dcm"))) == 2
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))


@pytest.mark.parametrize("change", ["edit_source", "add_source", "populate_destination"])
def test_publication_retry_rechecks_inputs_and_destination(
    tmp_path, dicom_factory, dicom_writer, monkeypatch, change,
):
    source, original, destination = create_source(tmp_path, dicom_factory, dicom_writer)
    real_rename = Path.rename
    attempts = []

    def locked_once(path, target):
        if target == destination:
            attempts.append(None)
            if len(attempts) == 1:
                raise windows_error(32)
        return real_rename(path, target)

    def change_during_wait(_):
        if change == "edit_source":
            original.write_bytes(original.read_bytes() + b"synthetic edit")
        elif change == "add_source":
            dicom_writer(dicom_factory(), source / "added.dcm")
        else:
            destination.mkdir()
            (destination / "keep.txt").write_text("Keep this file", encoding="utf-8")

    monkeypatch.setattr(Path, "rename", locked_once)
    monkeypatch.setattr(filesystem.time, "sleep", change_during_wait)
    report = process_batch(source, destination)
    assert not report.ok
    assert len(attempts) == 1
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))
    if change == "populate_destination":
        assert (destination / "keep.txt").read_text(encoding="utf-8") == "Keep this file"
        assert not list(destination.glob("*.dcm"))
    else:
        assert not destination.exists()


def test_cancelling_publication_wait_still_cleans_private_staging(
    tmp_path, dicom_factory, dicom_writer, monkeypatch,
):
    source, _, destination = create_source(tmp_path, dicom_factory, dicom_writer)
    cancel = Event()
    real_rename = Path.rename
    real_rmtree = anonymizer.shutil.rmtree
    attempts = []
    cleanups = []

    def locked(path, target):
        if target == destination:
            attempts.append(None)
            raise windows_error(32)
        return real_rename(path, target)

    def cancelled_wait(timeout):
        cancel.set()
        return True

    def cleanup_locked_once(path, *args, **kwargs):
        cleanups.append(None)
        if len(cleanups) == 1:
            raise windows_error(5)
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", locked)
    monkeypatch.setattr(cancel, "wait", cancelled_wait)
    monkeypatch.setattr(anonymizer.shutil, "rmtree", cleanup_locked_once)
    monkeypatch.setattr(filesystem.time, "sleep", lambda _: None)
    report = process_batch(source, destination, cancel=cancel)
    assert report.status == "CANCELLED"
    assert len(attempts) == 1
    assert len(cleanups) == 2
    assert not destination.exists()
    assert not list(tmp_path.glob(".opendicom-incomplete-*"))
    assert not any(finding.code == "CLEANUP_FAILED" for finding in report.findings)

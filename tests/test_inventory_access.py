"""Unreadable inventory entries fail without exposing patient filenames or paths."""

import errno
import json
import os
import stat
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from opendicom_deid.core import scanner
from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.models import Cancelled


def windows_error(number):
    error = OSError(errno.ENOENT, "SYNTHETIC-SENSITIVE-DETAIL", "SYNTHETIC-SENSITIVE-PATH")
    error.winerror = number
    return error


@pytest.mark.parametrize("method,code", [("lstat", 3), ("stat", 5), ("stat", 206)])
def test_os_access_errors_are_not_reported_as_special_files(tmp_path, monkeypatch, method, code):
    target = tmp_path / "SYNTHETIC-PRIVATE-NAME.dcm"
    target.touch()
    original = getattr(Path, method)

    def unavailable(path, *args, **kwargs):
        # lstat delegates to stat(follow_symlinks=False) on some Python builds.
        if path.name == target.name and kwargs.get("follow_symlinks", True):
            raise windows_error(code)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, method, unavailable)
    findings = []
    assert scanner._paths(tmp_path, True, findings, None) == []
    assert len(findings) == 1
    finding = findings[0]
    assert finding.code == "ENTRY_ACCESS" and finding.severity == "FAIL"
    assert finding.file_id == "Entry 000001"
    assert f"Windows error {code}" in finding.message and "errno 2" in finding.message
    for sensitive in (str(tmp_path), target.name, "SYNTHETIC-SENSITIVE"):
        assert sensitive not in finding.message


def test_unreadable_entry_blocks_batch_publication(tmp_path, dicom_factory, dicom_writer, monkeypatch):
    source = tmp_path / "input"
    dicom_writer(dicom_factory(), source / "a.dcm")
    inaccessible = source / "b-sensitive-name.dcm"
    inaccessible.touch()
    original = Path.lstat

    def unavailable(path, *args, **kwargs):
        if path.name == inaccessible.name:
            raise windows_error(3)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", unavailable)
    output = tmp_path / "output"
    report = process_batch(source, output)
    assert report.input_count == 1 and report.output_count == 0 and not report.ok
    assert not output.exists()
    assert any(f.code == "ENTRY_ACCESS" for f in report.findings)
    assert inaccessible.name not in json.dumps(report.to_dict())


def test_file_open_failure_reports_access_not_dicom_format(tmp_path, dicom_factory, dicom_writer, monkeypatch):
    path = dicom_writer(dicom_factory(), tmp_path / "PRIVATE-SOURCE.dcm")

    def denied(_):
        raise windows_error(5)

    monkeypatch.setattr(scanner, "file_digest", denied)
    scan = scanner.scan_directory(tmp_path)
    finding = next(f for f in scan.findings if f.code == "FILE_ACCESS")
    assert not scan.ok and finding.file_id == "File 000001"
    assert "Windows error 5" in finding.message
    assert path.name not in finding.message and "SYNTHETIC-SENSITIVE" not in finding.message
    assert not any(f.code == "DICOM_READ" for f in scan.findings)


def test_directory_listing_errors_keep_only_safe_os_details(tmp_path, monkeypatch):
    def broken_walk(root, *, followlinks, onerror):
        assert not followlinks
        onerror(windows_error(5))
        return iter(())

    monkeypatch.setattr(scanner.os, "walk", broken_walk)
    findings = []
    assert scanner._paths(tmp_path, True, findings, None) == []
    assert len(findings) == 1 and findings[0].code == "DIRECTORY_UNREADABLE"
    assert "Windows error 5" in findings[0].message
    assert "SYNTHETIC-SENSITIVE" not in findings[0].message


def test_name_surrogate_directories_are_excluded_before_traversal(tmp_path, monkeypatch):
    junction = tmp_path / "private-junction"
    junction.mkdir()
    (junction / "must-not-visit.dcm").touch()
    regular = tmp_path / "regular.dcm"
    regular.touch()
    original = Path.lstat

    def reparse(path, *args, **kwargs):
        if path == junction:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0xA0000003)
        assert path.name != "must-not-visit.dcm"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse)
    findings = []
    assert scanner._paths(tmp_path, True, findings, None) == [regular]
    assert len(findings) == 1 and findings[0].code == "SYMLINK"
    assert junction.name not in findings[0].message


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Host has no FIFO filesystem objects.")
def test_confirmed_special_file_keeps_distinct_diagnostic(tmp_path):
    os.mkfifo(tmp_path / "private-fifo")
    findings = []
    assert scanner._paths(tmp_path, True, findings, None) == []
    assert len(findings) == 1 and findings[0].code == "SPECIAL_FILE"
    assert "special filesystem object" in findings[0].message


def test_readable_inventory_order_and_recursion_are_unchanged(tmp_path):
    paths = [tmp_path / name for name in ("b.dcm", "a.dcm", "b-dir/z.dcm", "a-dir/c.dcm")]
    for path in paths:
        path.parent.mkdir(exist_ok=True)
        path.touch()
    findings = []
    assert scanner._paths(tmp_path, True, findings, None) == [paths[1], paths[0], paths[3], paths[2]]
    assert scanner._paths(tmp_path, False, findings, None) == [paths[1], paths[0]]
    assert not findings


def test_directory_that_changes_type_is_not_silently_omitted(tmp_path, monkeypatch):
    changed = tmp_path / "changing-private-directory"
    changed.mkdir()
    (changed / "must-not-visit.dcm").touch()
    original = Path.stat

    def became_regular_file(path, *args, **kwargs):
        if path == changed and kwargs.get("follow_symlinks", True):
            return SimpleNamespace(st_mode=stat.S_IFREG)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", became_regular_file)
    findings = []
    assert scanner._paths(tmp_path, True, findings, None) == []
    assert len(findings) == 1 and findings[0].code == "ENTRY_CHANGED"
    assert changed.name not in findings[0].message


def test_inventory_checks_cancellation_between_entries(tmp_path, monkeypatch):
    for name in ("a.dcm", "b.dcm"):
        (tmp_path / name).touch()
    cancel = Event()
    original = Path.lstat

    def stop_after_first(path, *args, **kwargs):
        assert path.name != "b.dcm"
        cancel.set()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", stop_after_first)
    with pytest.raises(Cancelled):
        scanner._paths(tmp_path, True, [], cancel)

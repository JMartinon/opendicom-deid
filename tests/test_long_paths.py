"""Portable path contracts and real Windows long-path transaction acceptance."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pydicom
import pytest
from conftest import reference

from opendicom_deid.cli import report_path_allowed
from opendicom_deid.core import filesystem
from opendicom_deid.core.anonymizer import DirectoryError, check_directories, process_batch
from opendicom_deid.core.models import Options
from opendicom_deid.core.privacy_inspection import inspect_privacy
from opendicom_deid.core.report import Report
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.storage_inspection import inspect_storage
from opendicom_deid.core.validator import validate_directory


@pytest.mark.parametrize(("source", "expected"), [
    (r"C:\Export\Case", r"\\?\C:\Export\Case"),
    ("c:/Export/../Input/./Case", r"\\?\c:\Input\Case"),
    (r"\\server\share\export\case", r"\\?\UNC\server\share\export\case"),
    (r"\\server\share\export\..\case", r"\\?\UNC\server\share\case"),
    (r"\\?\C:\Export\Case", r"\\?\C:\Export\Case"),
    (r"\\?\UNC\server\share\case", r"\\?\UNC\server\share\case"),
    ("\\\\?\\C:\\folder.\\tail ", "\\\\?\\C:\\folder.\\tail "),
])
def test_windows_path_conversion_is_absolute_and_idempotent(source, expected):
    converted = filesystem._extended_windows_path(source)
    assert converted == expected
    assert filesystem._extended_windows_path(converted) == converted


@pytest.mark.parametrize("source", [
    "relative/case", r"C:case", r"\case", r"\\server",
    r"\\.\PhysicalDrive0", r"\\?\GLOBALROOT\Device\HarddiskVolume1\case",
    r"\\?\Volume{00000000-0000-0000-0000-000000000000}\case", "C:\\bad\x00name",
])
def test_non_filesystem_or_non_absolute_windows_names_are_rejected(source):
    with pytest.raises(ValueError):
        filesystem._extended_windows_path(source)


@pytest.mark.skipif(os.name == "nt", reason="POSIX path semantics")
def test_posix_resolution_keeps_normal_path_and_link_semantics(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    link = tmp_path / "link"
    link.symlink_to(source, target_is_directory=True)
    assert filesystem.filesystem_path(link) == source.resolve()
    assert filesystem.filesystem_path(link, resolve=False).is_symlink()
    with pytest.raises(FileNotFoundError):
        filesystem.filesystem_path(source / "missing", strict=True)


@pytest.fixture
def windows_tree(tmp_path):
    if os.name != "nt":
        pytest.skip("Requires native Windows filesystem APIs")
    # Make the fixture independent of the conversion being tested. Explicit
    # extended syntax also lets cleanup work on hosts without a long-path policy.
    raw = str(tmp_path.absolute())
    extended = Path("\\\\?\\UNC\\" + raw[2:] if raw.startswith("\\\\") else "\\\\?\\" + raw)
    try:
        yield tmp_path, extended
    finally:
        shutil.rmtree(extended)


def deep_path(root, branch):
    result = root / branch
    for index in range(5):
        result /= f"synthetic-level-{index}-" + "x" * 48
    return result


@pytest.mark.parametrize(("prefixed_input", "prefixed_output", "workers"), [
    (False, False, 1), (True, False, 2), (False, True, 2), (True, True, 1),
])
def test_native_windows_long_paths_preserve_full_rt_batch(
    windows_tree, dicom_factory, dicom_writer, prefixed_input, prefixed_output, workers,
):
    plain_root, extended_root = windows_tree
    plain_input = deep_path(plain_root, "input")
    plain_output = deep_path(plain_root, "output")
    source = deep_path(extended_root, "input")
    output = deep_path(extended_root, "output")
    assert len(str(plain_input)) > 300 and len(str(plain_output)) > 300
    image = dicom_factory("CT")
    struct = dicom_factory("RTSTRUCT", study=image.StudyInstanceUID)
    plan = dicom_factory("RTPLAN", study=image.StudyInstanceUID)
    struct.ReferencedImageSequence = [reference(image)]
    plan.ReferencedStructureSetSequence = [reference(struct)]
    files = [dicom_writer(ds, source / filename) for ds, filename in (
        (image, "a.dcm"), (struct, "b.dcm"), (plan, "c.dcm"),
    )]
    before = {file: hashlib.sha256(file.read_bytes()).digest() for file in files}
    requested_input = source if prefixed_input else plain_input
    requested_output = output if prefixed_output else plain_output

    scan = scan_directory(requested_input)
    assert scan.ok and scan.total == 3, scan.findings
    assert [record.file_id for record in scan.files] == ["File 000001", "File 000002", "File 000003"]
    assert inspect_storage(requested_input, file_id=1)["files"][0]["status"] == "INSPECTED"
    assert inspect_privacy(requested_input, file_id=1)["status"] == "REVIEW_REQUIRED"
    report = process_batch(requested_input, requested_output, options=Options(workers=workers))
    assert report.ok and report.output_count == 3, report.to_dict()
    assert before == {file: hashlib.sha256(file.read_bytes()).digest() for file in files}
    saved = {ds.Modality: ds for ds in (pydicom.dcmread(file) for file in output.rglob("*.dcm"))}
    assert set(saved) == {"CT", "RTSTRUCT", "RTPLAN"}
    assert saved["CT"].PixelData == image.PixelData
    assert saved["RTSTRUCT"].ReferencedImageSequence[0].ReferencedSOPInstanceUID == saved["CT"].SOPInstanceUID
    assert saved["RTPLAN"].ReferencedStructureSetSequence[0].ReferencedSOPInstanceUID == saved["RTSTRUCT"].SOPInstanceUID
    assert validate_directory(plain_output).ok
    assert not list(output.parent.glob(".opendicom-incomplete-*"))

    plain_report = deep_path(plain_root, "report") / "result.json"
    saved_report = deep_path(extended_root, "report") / "result.json"
    saved_report.parent.mkdir(parents=True)
    assert report_path_allowed(plain_report, requested_input)
    report.save(plain_report)
    assert '"status": "COMPLETED"' in saved_report.read_text()
    with pytest.raises(FileExistsError):
        Report().save(plain_report)


@pytest.mark.parametrize("prefixed_input", [False, True])
@pytest.mark.parametrize("relationship", ["same", "child", "parent"])
def test_mixed_windows_spelling_cannot_bypass_source_output_separation(
    windows_tree, prefixed_input, relationship,
):
    plain_root, extended_root = windows_tree
    source = extended_root / "source"
    source.mkdir()
    plain_source = plain_root / "source"
    requested_input = source if prefixed_input else plain_source
    opposite = plain_source if prefixed_input else source
    target = {"same": opposite, "child": opposite / "new-output", "parent": opposite.parent}[relationship]
    with pytest.raises(DirectoryError):
        check_directories(requested_input, target)
    assert not report_path_allowed(opposite / "report.json", requested_input)


def test_output_junction_is_not_followed_for_publication(windows_tree):
    plain_root, extended_root = windows_tree
    source, elsewhere = extended_root / "source", extended_root / "elsewhere"
    source.mkdir()
    elsewhere.mkdir()
    plain_link = plain_root / "output-junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(plain_link), str(plain_root / "elsewhere")],
        capture_output=True, check=False,
    )
    if result.returncode:
        pytest.skip("This Windows host does not permit creating a synthetic junction")
    with pytest.raises(DirectoryError):
        check_directories(source, plain_link)
    assert not list(elsewhere.iterdir())


@pytest.mark.parametrize("prefixed_input", [False, True])
def test_new_folder_safety_uses_same_windows_path_spelling(windows_tree, prefixed_input):
    pytest.importorskip("tkinter")
    from opendicom_deid.gui.app import create_output_folder

    plain_root, extended_root = windows_tree
    plain_source = deep_path(plain_root, "source")
    source = deep_path(extended_root, "source")
    source.mkdir(parents=True)
    requested_source = source if prefixed_input else plain_source
    parent = plain_source if prefixed_input else source
    with pytest.raises(ValueError):
        create_output_folder(str(parent), "inside-source", str(requested_source))
    assert not (source / "inside-source").exists()

    output_parent = deep_path(extended_root, "new-folder")
    output_parent.mkdir(parents=True)
    created = create_output_folder(
        str(deep_path(plain_root, "new-folder")), "output", str(requested_source),
    )
    assert created.is_dir()
    assert created == filesystem.filesystem_path(output_parent / "output")

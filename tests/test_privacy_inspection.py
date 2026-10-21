"""Synthetic metadata screens report presence and limits, never source values."""

from __future__ import annotations

import json
import logging
import struct
import warnings
from threading import Event

import pydicom
import pytest
from conftest import make_dataset, reference, write_dataset
from pydicom.dataset import Dataset
from pydicom.uid import DeflatedExplicitVRLittleEndian, generate_uid

from opendicom_deid.cli import main
from opendicom_deid.core import privacy_inspection, scanner
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.sop_classes import VIEWRAY_RT_PLAN_STORAGE

SECRET = "SYNTHETIC-CONFIDENTIAL-4519"


def _plan():
    dataset = make_dataset(
        "RTPLAN", patient_name="SyntheticSecret^Patient", patient_id=SECRET,
    )
    dataset.SOPClassUID = VIEWRAY_RT_PLAN_STORAGE
    dataset.file_meta.MediaStorageSOPClassUID = VIEWRAY_RT_PLAN_STORAGE
    return dataset


def _assert_review(result):
    assert result["status"] == "REVIEW_REQUIRED", result
    assert result["inspection_type"] == "privacy"
    assert result["privacy_review"] == "REQUIRED"
    assert result["coverage"] == "LIMITED"
    assert result["limitations"]


def _assert_no_source_values(result, dataset, path, captured=""):
    rendered = json.dumps(result) + captured
    for value in (
        SECRET, "SyntheticSecret", str(dataset.SOPInstanceUID),
        str(dataset.StudyInstanceUID), str(dataset.SeriesInstanceUID),
        str(dataset.FrameOfReferenceUID), str(path), path.name,
    ):
        assert value not in rendered


def test_standard_and_nested_identity_presence_is_reported_without_values(tmp_path, capsys):
    plan = _plan()
    nested = Dataset()
    nested.OperatorsName = "NestedSyntheticSecret^Operator"
    nested.StudyDescription = SECRET
    plan.RequestAttributesSequence = [nested]
    source = tmp_path / "synthetic-secret-filename.dcm"
    write_dataset(plan, source)
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    _assert_review(result)
    assert result["file_id"] == "File 000001"
    assert result["summary"]["direct_identifiers"] >= 2
    assert result["summary"]["person_names"] >= 1
    assert result["summary"]["dates"] >= 1
    assert result["summary"]["free_text"] >= 1
    keywords = {finding["keyword"] for finding in result["findings"]}
    assert {"PatientName", "PatientID", "OperatorsName"} <= keywords
    operator = next(f for f in result["findings"] if f["keyword"] == "OperatorsName")
    assert "[0]" in operator["tag_path"]
    captured = capsys.readouterr()
    _assert_no_source_values(result, plan, source, captured.out + captured.err)
    assert "NestedSyntheticSecret" not in json.dumps(result)


@pytest.mark.parametrize("payload", ["text", "binary", "sequence"])
def test_private_identity_matches_remain_unresolved_and_never_echo_values(tmp_path, payload):
    plan = _plan()
    plan.add_new(0x01F10010, "LO", "SYNTHETIC PRIVATE")
    if payload == "text":
        plan.add_new(0x01F11001, "LO", "Export identity " + SECRET)
    elif payload == "binary":
        plan.add_new(0x01F11001, "OB", b"opaque prefix " + SECRET.encode() + b"\0")
    else:
        item = Dataset()
        item.PatientName = plan.PatientName
        item.add_new(0x01F11002, "LO", SECRET)
        plan.add_new(0x01F11001, "SQ", [item])
    source = write_dataset(plan, tmp_path / "synthetic-secret-file.dcm")
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    _assert_review(result)
    assert result["summary"]["private_elements"] >= 2
    assert result["summary"]["private_identifier_matches"] >= 1
    assert any(f["code"] == "PRIVATE_IDENTIFIER_MATCH" for f in result["findings"])
    assert any(f["code"] == "PRIVATE_CONTENT_UNRESOLVED" for f in result["findings"])
    _assert_no_source_values(result, plan, source)


def test_opaque_and_malformed_private_fd_are_unresolved_not_safe(tmp_path, capsys, caplog):
    plan = _plan()
    plan.add_new(0x01F10010, "LO", "VIEWRAY")
    plan.add_new(0x01F11001, "OB", b"\x00\xff\x10\x99")
    source = write_dataset(plan, tmp_path / "synthetic-secret-file.dcm")
    with source.open("ab") as stream:
        stream.write(struct.pack("<HH2sH", 0x01F1, 0x1026, b"FD", 6) + b"0.938 ")
    before = source.read_bytes()
    old_setting = pydicom.config.convert_wrong_length_to_UN
    pydicom.config.convert_wrong_length_to_UN = False
    try:
        result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
        _assert_review(result)
        assert result["summary"]["private_elements"] >= 3
        assert any(f["code"] == "PRIVATE_CONTENT_UNRESOLVED" for f in result["findings"])
        assert pydicom.config.convert_wrong_length_to_UN is False
    finally:
        pydicom.config.convert_wrong_length_to_UN = old_setting
    assert source.read_bytes() == before
    captured = capsys.readouterr()
    _assert_no_source_values(result, plan, source, captured.out + captured.err + caplog.text)


def test_missing_direct_identity_and_deidentification_marker_never_prove_privacy(tmp_path):
    plan = _plan()
    for keyword in (
        "PatientName", "PatientID", "PatientBirthDate", "ReferringPhysicianName",
        "AccessionNumber", "InstitutionName",
    ):
        delattr(plan, keyword)
    plan.PatientIdentityRemoved = "YES"
    plan.DeidentificationMethod = "SYNTHETIC TEST MARKER"
    plan.add_new(0x01F11001, "OB", b"unknown proprietary payload")
    write_dataset(plan, tmp_path / "synthetic.dcm")
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    _assert_review(result)
    assert result["summary"]["entity_uids"] >= 4
    assert result["summary"]["private_elements"] >= 1
    assert not any(f.get("severity") == "PASS" for f in result["findings"])


def test_entity_and_nested_reference_uids_are_counted_without_revealing_them(tmp_path):
    plan = _plan()
    target = make_dataset()
    plan.ReferencedImageSequence = [reference(target)]
    source = write_dataset(plan, tmp_path / "synthetic-secret.dcm")
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    _assert_review(result)
    assert result["summary"]["entity_uids"] >= 5
    assert any(f["keyword"] == "ReferencedSOPInstanceUID" for f in result["findings"])
    _assert_no_source_values(result, plan, source)
    assert str(target.SOPInstanceUID) not in json.dumps(result)


def test_screening_does_not_modify_input_or_create_files(tmp_path):
    plan = _plan()
    source = write_dataset(plan, tmp_path / "synthetic.dcm")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    _assert_review(privacy_inspection.inspect_privacy(tmp_path, file_id=1))
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after
    assert source.exists()


@pytest.mark.parametrize("diagnostic", ["warning", "log", "exception"])
def test_parser_diagnostics_do_not_escape_result_stdout_or_logs(
    tmp_path, monkeypatch, capsys, caplog, diagnostic,
):
    plan = _plan()
    source = write_dataset(plan, tmp_path / "synthetic-secret.dcm")

    def read(*args, **kwargs):
        if diagnostic == "exception":
            raise ValueError(SECRET + str(source))
        if diagnostic == "warning":
            warnings.warn(SECRET + str(source), UserWarning)
        else:
            logging.getLogger("pydicom").warning(SECRET + str(source))
        return plan

    monkeypatch.setattr(privacy_inspection.pydicom, "dcmread", read)
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    assert result["status"] in {"FAILED", "REVIEW_REQUIRED"}
    captured = capsys.readouterr()
    _assert_no_source_values(result, plan, source, captured.out + captured.err + caplog.text)


@pytest.mark.parametrize("number", [0, -1, True, 1.5, SECRET, 2])
def test_invalid_file_number_has_safe_failure(tmp_path, number):
    write_dataset(_plan(), tmp_path / "synthetic-secret.dcm")
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=number)
    assert result["status"] == "FAILED"
    assert SECRET not in json.dumps(result)
    assert "synthetic-secret" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_missing_directory_has_safe_failure(tmp_path):
    result = privacy_inspection.inspect_privacy(tmp_path / SECRET, file_id=1)
    assert result["status"] == "FAILED"
    assert SECRET not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_excluded_symlink_inventory_does_not_guess_selected_file(tmp_path):
    source = write_dataset(_plan(), tmp_path / "b.dcm")
    try:
        (tmp_path / (SECRET + ".dcm")).symlink_to(source)
    except OSError:
        pytest.skip("Symbolic links are unavailable.")
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    assert result["status"] == "FAILED"
    assert SECRET not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_file_number_matches_scan_and_recursion_setting(tmp_path):
    (tmp_path / "a-note.txt").write_text(SECRET)
    write_dataset(make_dataset("MR"), tmp_path / "b.dcm")
    write_dataset(make_dataset(), tmp_path / "nested" / "c.dcm")
    scan = scan_directory(tmp_path)
    assert [record.file_id for record in scan.files] == ["File 000002", "File 000003"]
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=3)
    _assert_review(result)
    assert result["file_id"] == scan.files[1].file_id
    shallow = privacy_inspection.inspect_privacy(tmp_path, file_id=3, recursive=False)
    assert shallow["status"] == "FAILED"


def test_cancelled_before_inventory_does_not_read(tmp_path, monkeypatch):
    cancel = Event()
    cancel.set()

    def unexpected(*args, **kwargs):
        pytest.fail("Cancelled inspection must not read source metadata.")

    monkeypatch.setattr(privacy_inspection.pydicom, "dcmread", unexpected)
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1, cancel=cancel)
    assert result["status"] == "CANCELLED"
    assert "findings" not in result


def test_cancellation_during_inventory_prevents_metadata_read(tmp_path, monkeypatch):
    write_dataset(_plan(), tmp_path / "a.dcm")
    write_dataset(_plan(), tmp_path / "nested" / "b.dcm")
    cancel = Event()
    walk = scanner.os.walk

    def cancelled_walk(*args, **kwargs):
        for entry in walk(*args, **kwargs):
            yield entry
            cancel.set()

    def unexpected(*args, **kwargs):
        pytest.fail("Cancelled inventory must not inspect metadata.")

    monkeypatch.setattr(scanner.os, "walk", cancelled_walk)
    monkeypatch.setattr(privacy_inspection.pydicom, "dcmread", unexpected)
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1, cancel=cancel)
    assert result["status"] == "CANCELLED"
    assert "findings" not in result


def test_cancellation_after_read_discards_results(tmp_path, monkeypatch):
    write_dataset(_plan(), tmp_path / "synthetic.dcm")
    cancel = Event()
    read = privacy_inspection.pydicom.dcmread

    def cancel_after_read(*args, **kwargs):
        dataset = read(*args, **kwargs)
        cancel.set()
        return dataset

    monkeypatch.setattr(privacy_inspection.pydicom, "dcmread", cancel_after_read)
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1, cancel=cancel)
    assert result["status"] == "CANCELLED"
    assert "findings" not in result


def test_untrusted_file_meta_and_preamble_never_escape_report(tmp_path):
    plan = _plan()
    plan.preamble = SECRET.encode().ljust(128, b"\0")
    plan.file_meta.PrivateInformationCreatorUID = generate_uid()
    plan.file_meta.PrivateInformation = SECRET.encode()
    source = write_dataset(plan, tmp_path / "synthetic-secret.dcm")
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    _assert_review(result)
    _assert_no_source_values(result, plan, source)
    assert str(plan.file_meta.PrivateInformationCreatorUID) not in json.dumps(result)


def test_cli_completed_inspection_is_json_with_review_required(tmp_path, capsys):
    plan = _plan()
    source = write_dataset(plan, tmp_path / "synthetic-secret.dcm")
    assert main(["inspect-privacy", str(tmp_path), "--file-id", "1"]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    result = json.loads(captured.out)
    _assert_review(result)
    _assert_no_source_values(result, plan, source, captured.out + captured.err)


@pytest.mark.parametrize("invalid", ["number", "directory"])
def test_cli_invalid_selection_returns_failure_without_paths(tmp_path, capsys, invalid):
    write_dataset(_plan(), tmp_path / "synthetic-secret.dcm")
    directory = tmp_path / SECRET if invalid == "directory" else tmp_path
    number = "1" if invalid == "directory" else "0"
    assert main(["inspect-privacy", str(directory), "--file-id", number]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "FAILED"
    assert not captured.err
    assert SECRET not in captured.out
    assert str(tmp_path) not in captured.out


def test_cli_cancellation_returns_130_and_passes_recursion_choice(tmp_path, monkeypatch, capsys):
    def cancelled(directory, file_id, recursive=True, cancel=None):
        assert directory == tmp_path
        assert file_id == 199
        assert recursive is False
        assert isinstance(cancel, Event)
        return {"status": "CANCELLED", "message": "Inspection was cancelled."}

    monkeypatch.setattr(privacy_inspection, "inspect_privacy", cancelled)
    assert main([
        "inspect-privacy", str(tmp_path), "--file-id", "199", "--no-recursive",
    ]) == 130
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "CANCELLED"
    assert not captured.err


def test_metadata_screen_stops_before_pixels(tmp_path, monkeypatch):
    write_dataset(make_dataset(), tmp_path / "synthetic.dcm")
    read = privacy_inspection.pydicom.dcmread
    inspected = []

    def read_header(*args, **kwargs):
        assert kwargs["stop_before_pixels"] is True
        dataset = read(*args, **kwargs)
        assert "PixelData" not in dataset
        inspected.append(dataset)
        return dataset

    monkeypatch.setattr(privacy_inspection.pydicom, "dcmread", read_header)
    _assert_review(privacy_inspection.inspect_privacy(tmp_path, file_id=1))
    assert len(inspected) == 1


def test_file_size_limit_prevents_metadata_parsing(tmp_path, monkeypatch):
    write_dataset(_plan(), tmp_path / "synthetic-secret.dcm")
    monkeypatch.setattr(privacy_inspection, "MAX_FILE_BYTES", 1)

    def unexpected(*args, **kwargs):
        pytest.fail("An oversized file must be rejected before parsing.")

    monkeypatch.setattr(privacy_inspection.pydicom, "dcmread", unexpected)
    monkeypatch.setattr(privacy_inspection, "read_file_meta_info", unexpected)
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    assert result["status"] == "FAILED"
    assert "synthetic-secret" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_finding_limit_is_visible_and_preserves_summary_counts(tmp_path, monkeypatch):
    write_dataset(_plan(), tmp_path / "synthetic.dcm")
    monkeypatch.setattr(privacy_inspection, "MAX_FINDINGS", 2)
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    _assert_review(result)
    assert len(result["findings"]) == 2
    assert result["findings_truncated"] is True
    assert sum(result["summary"].values()) > 2


@pytest.mark.parametrize("limit", ["MAX_ELEMENTS", "MAX_DEPTH", "MAX_ITEMS", "MAX_VALUE_BYTES"])
def test_traversal_and_value_limits_explicitly_mark_partial_coverage(tmp_path, monkeypatch, limit):
    plan = _plan()
    nested = Dataset()
    nested.PatientName = "SyntheticSecret^Nested"
    plan.RequestAttributesSequence = [nested, Dataset()]
    plan.add_new(0x01F11001, "OB", b"opaque" * 20)
    source = write_dataset(plan, tmp_path / "synthetic-secret.dcm")
    monkeypatch.setattr(privacy_inspection, limit, 0 if limit == "MAX_DEPTH" else 1)
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    _assert_review(result)
    assert result["limits_reached"] is True
    assert any(f["code"] == "INSPECTION_LIMIT" for f in result["findings"])
    _assert_no_source_values(result, plan, source)


def test_short_identifier_substrings_are_not_reported_as_private_matches(tmp_path):
    plan = _plan()
    plan.PatientID = "123"
    plan.PatientName = "A^B"
    plan.add_new(0x01F11001, "OB", b"binary123A Bcontent")
    write_dataset(plan, tmp_path / "synthetic.dcm")
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    _assert_review(result)
    assert result["summary"]["private_identifier_matches"] == 0
    assert result["summary"]["private_elements"] >= 1


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_private_utf16_identifier_matches_are_candidates_without_values(tmp_path, encoding):
    plan = _plan()
    plan.add_new(0x01F11001, "OB", SECRET.encode(encoding))
    source = write_dataset(plan, tmp_path / "synthetic-secret.dcm")
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    _assert_review(result)
    assert result["summary"]["private_identifier_matches"] >= 1
    _assert_no_source_values(result, plan, source)


def test_deflated_dataset_is_left_for_specialized_review_without_inflation(tmp_path, monkeypatch):
    plan = _plan()
    plan.file_meta.TransferSyntaxUID = DeflatedExplicitVRLittleEndian
    write_dataset(plan, tmp_path / "synthetic.dcm")

    def unexpected(*args, **kwargs):
        pytest.fail("A deflated dataset must not be expanded for metadata screening.")

    monkeypatch.setattr(privacy_inspection.pydicom, "dcmread", unexpected)
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1)
    assert result["status"] == "FAILED"
    assert result["privacy_review"] == "REQUIRED"


def test_cancellation_during_element_screening_discards_partial_findings(tmp_path, monkeypatch):
    write_dataset(_plan(), tmp_path / "synthetic.dcm")
    cancel = Event()
    get_item = Dataset.get_item
    encountered = []

    def cancel_on_identifier(dataset, key, *args, **kwargs):
        item = get_item(dataset, key, *args, **kwargs)
        if int(key) == 0x00100020:
            encountered.append(key)
            cancel.set()
        return item

    monkeypatch.setattr(Dataset, "get_item", cancel_on_identifier)
    result = privacy_inspection.inspect_privacy(tmp_path, file_id=1, cancel=cancel)
    assert encountered
    assert result["status"] == "CANCELLED"
    assert "findings" not in result
    assert "summary" not in result

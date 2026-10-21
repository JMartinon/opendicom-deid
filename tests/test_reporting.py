"""Readable summaries retain complete, neutral per-file JSON diagnostics."""

import json

from opendicom_deid.core.models import Finding
from opendicom_deid.core.report import Report, format_findings


def test_repeated_failures_are_grouped_but_json_keeps_every_finding(tmp_path):
    message = "The modality is missing or inconsistent with the SOP class."
    findings = [
        Finding("FAIL", "INVALID_MODALITY", message, f"file-{index:06d}")
        for index in range(1, 101)
    ]
    report = Report(input_count=100, findings=findings)

    summary = report.summary()
    assert summary.count(message) == 1
    assert f"FAIL: {message} (100 occurrences; 100 files)" in summary
    assert "Checks: 0 passed, 0 warnings, 100 failed" in summary
    assert "file-000001" not in summary
    assert not report.ok

    destination = tmp_path / "report.json"
    report.save(destination)
    saved = json.loads(destination.read_text())
    assert len(saved["findings"]) == 100
    assert saved["findings"][0] == {
        "severity": "FAIL", "code": "INVALID_MODALITY", "message": message,
        "file_id": "file-000001",
    }
    assert saved["findings"][-1]["file_id"] == "file-000100"
    assert report.findings == findings
    # Export contains only the fixed diagnostic and neutral identifiers.
    assert str(tmp_path) not in destination.read_text()


def test_grouping_counts_distinct_files_and_handles_batch_findings():
    findings = [
        Finding("WARNING", "REVIEW", "Review required.", "file-000001"),
        Finding("WARNING", "REVIEW", "Review required.", "file-000001"),
        Finding("FAIL", "BATCH", "Batch failed."),
        Finding("FAIL", "BATCH", "Batch failed."),
    ]
    assert format_findings(findings) == (
        "WARNING: Review required. (2 occurrences; 1 file)\n"
        "FAIL: Batch failed. (2 occurrences)"
    )


def test_grouping_preserves_first_order_and_distinguishes_check_identity():
    findings = [
        Finding("WARNING", "FIRST", "Shared message.", "file-000001"),
        Finding("FAIL", "FIRST", "Shared message.", "file-000002"),
        Finding("WARNING", "SECOND", "Shared message.", "file-000003"),
        Finding("WARNING", "FIRST", "Different message.", "file-000004"),
        Finding("WARNING", "FIRST", "Shared message.", "file-000005"),
    ]
    assert format_findings(iter(findings)).splitlines() == [
        "WARNING: Shared message. (2 occurrences; 2 files)",
        "FAIL: Shared message.",
        "WARNING: Shared message.",
        "WARNING: Different message.",
    ]


def test_empty_findings_have_no_blank_diagnostic_line():
    assert format_findings([]) == ""
    assert "\n\n" not in Report().summary()

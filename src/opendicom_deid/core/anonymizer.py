"""Transactional two-pass processing, with batch-local identities and mandatory validation."""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from pathlib import Path
from threading import Event
from uuid import uuid4

from pydicom import dcmwrite
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRBigEndian, ExplicitVRLittleEndian, ImplicitVRLittleEndian

from .diagnostics import private_diagnostics
from .filesystem import retry_windows_file_lock, safe_error_details
from .models import (
    Cancelled,
    ExpectedOutput,
    Finding,
    Options,
    Progress,
    ProgressCallback,
    check_cancel,
)
from .policy import PolicyError, scrub_metadata
from .reader import ReadError, file_digest, read_dicom
from .reference_classes import apply_reference_class_repairs, reference_class_repairs
from .report import Report
from .scanner import _paths, scan_directory
from .uid_mapper import UIDMapper
from .validator import validate_batch

IMPLEMENTATION_UID = "2.25.317044807189617148619733228275840119817"


class DirectoryError(ValueError):
    """Safe fixed directory-configuration message."""


def check_directories(input_dir: str | Path, output_dir: str | Path) -> tuple[Path, Path]:
    """Require disjoint trees and an unused destination; never overwrite a clinical source."""
    source = Path(input_dir).expanduser().resolve()
    requested = Path(output_dir).expanduser()
    target = requested.resolve()
    if not source.is_dir():
        raise DirectoryError("Select a readable input directory.")
    if source == target or source in target.parents or target in source.parents:
        raise DirectoryError("Input and output must be separate directories, outside each other's trees.")
    if requested.is_symlink():
        raise DirectoryError("The output directory cannot be a symbolic link.")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise DirectoryError("Choose a new or empty output directory.")
    return source, target


def _reset_file_meta(ds: Dataset) -> None:
    """Replace preamble and every file-meta field except the actual encoding semantics."""
    previous_meta = getattr(ds, "file_meta", Dataset())
    transfer = previous_meta.get("TransferSyntaxUID")
    if not transfer:
        implicit, little = ds.original_encoding[:2]
        transfer = ImplicitVRLittleEndian if implicit else (
            ExplicitVRLittleEndian if little else ExplicitVRBigEndian
        )
    meta = FileMetaDataset()
    meta.FileMetaInformationVersion = b"\x00\x01"
    meta.MediaStorageSOPClassUID = ds.SOPClassUID
    meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    meta.TransferSyntaxUID = transfer
    meta.ImplementationClassUID = IMPLEMENTATION_UID
    meta.ImplementationVersionName = "OPENDICOM_010"
    ds.file_meta = meta
    ds.preamble = b"\x00" * 128


def _write(ds: Dataset, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with private_diagnostics() as diagnostics:
        # Only retry opening a new file. Once writing starts, any failure must
        # abort the transaction; replaying a partially written file is unsafe.
        with retry_windows_file_lock(lambda: target.open("xb")) as stream:
            dcmwrite(stream, ds, enforce_file_format=True)
            stream.flush()
            os.fsync(stream.fileno())
    if diagnostics.has_warnings:
        raise ValueError("Writing produced a DICOM metadata warning.")


def process_batch(
    input_dir: str | Path,
    output_dir: str | Path,
    options: Options | None = None,
    cancel: Event | None = None,
    progress: ProgressCallback | None = None,
) -> Report:
    """Process into private staging and publish only after source/output comparison passes.

    All mappings are held in memory for this call only. Public errors are fixed messages;
    exception text, paths, UIDs, and patient values are never added to the report.
    """
    options = options or Options()
    report = Report()
    staging: Path | None = None
    current_label: str | None = None
    stage = "checking directories"
    try:
        check_cancel(cancel)
        source, destination = check_directories(input_dir, output_dir)
        stage = "scanning input"
        scan = scan_directory(
            source, options.recursive, cancel, progress,
            allow_viewray_public_plan=options.allow_viewray_public_plan,
        )
        report.input_count = scan.total
        report.modalities = scan.modalities
        report.findings.extend(scan.findings)
        if not scan.ok:
            return report
        stage = "preparing identifiers"
        class_repairs = reference_class_repairs(scan.files, enabled=options.allow_viewray_public_plan)
        uid_map = UIDMapper(
            value for record in scan.files
            for values in record.snapshot.entity_uids.values() for value in values
        )
        identifiers = frozenset(
            token for record in scan.files for token in record.snapshot.identifiers
        )
        identities = dict.fromkeys(record.patient_key for record in scan.files)
        pseudonyms: dict[tuple[str, ...], str] = {}
        for identity in identities:
            candidate = f"{options.patient_prefix}-{uuid4().hex}"
            while candidate in pseudonyms.values():
                candidate = f"{options.patient_prefix}-{uuid4().hex}"
            pseudonyms[identity] = candidate
        offsets = {
            identity: secrets.choice((-1, 1)) * (1 + secrets.randbelow(3650))
            if options.date_mode == "shift" else 0 for identity in identities
        }
        stage = "creating temporary output"
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".opendicom-incomplete-", dir=destination.parent))
        # Source grouping is reconstructed using only neutral ordinals at each level.
        groups: dict[Path, Path] = {Path("."): Path(".")}
        counts: dict[Path, int] = {}
        directory_patients: dict[Path, set[tuple[str, ...]]] = {}
        if options.organization == "source":
            for record in scan.files:
                directory_patients.setdefault(record.relative_parent, set()).add(record.patient_key)

        def safe_parent(relative: Path) -> Path:
            if relative in groups:
                return groups[relative]
            parent = safe_parent(relative.parent)
            counts[relative.parent] = counts.get(relative.parent, 0) + 1
            groups[relative] = parent / f"group-{counts[relative.parent]:04d}"
            return groups[relative]

        expected: list[ExpectedOutput] = []
        if progress:
            progress(Progress("De-identifying", 0, scan.total))
        if options.workers > 1 and scan.total > 1:
            from .parallel import FileTask, WorkerState, transform_parallel

            # Allocate every destination in scan order, before workers start.
            # Shared maps are sealed; workers never choose identities or paths.
            tasks = []
            for index, record in enumerate(scan.files, 1):
                check_cancel(cancel)
                pseudonym = pseudonyms[record.patient_key]
                offset = offsets[record.patient_key]
                parent = safe_parent(record.relative_parent) if options.organization == "source" else Path(".")
                if options.organization == "source" and len(directory_patients[record.relative_parent]) > 1:
                    parent /= pseudonym
                target = staging / parent / f"{uid_map[record.snapshot.sop_uid]}-{index:06d}.dcm"
                expected.append(ExpectedOutput(record, target, pseudonym, offset))
                tasks.append(FileTask(
                    record.path, target, record.sha256, record.file_id, pseudonym, offset,
                    class_repairs.get(record.file_id, {}),
                ))
            stage = "processing files in parallel"
            finding = transform_parallel(
                tasks, WorkerState(dict(uid_map), identifiers, options),
                cancel=cancel, progress=progress,
            )
            if finding is not None:
                report.findings.append(finding)
                return report
        else:
            for index, record in enumerate(scan.files, 1):
                current_label = record.file_id
                check_cancel(cancel)
                stage = "checking source files"
                if file_digest(record.path) != record.sha256:
                    report.findings.append(Finding(
                        "FAIL", "SOURCE_CHANGED", "An input file changed after the scan.", current_label
                    ))
                    return report
                stage = "reading source files"
                ds = read_dicom(record.path)
                if file_digest(record.path) != record.sha256:
                    report.findings.append(Finding(
                        "FAIL", "SOURCE_CHANGED", "An input file changed during processing.", current_label
                    ))
                    return report
                pseudonym = pseudonyms[record.patient_key]
                offset = offsets[record.patient_key]
                stage = "transforming metadata"
                with private_diagnostics() as diagnostics:
                    apply_reference_class_repairs(ds, class_repairs.get(record.file_id, {}))
                    scrub_metadata(
                        ds, pseudonym=pseudonym, uid_mapping=uid_map,
                        date_mode=options.date_mode, date_offset_days=offset,
                        known_identifiers=identifiers,
                        text_policy=options.text_policy,
                    )
                    _reset_file_meta(ds)
                if diagnostics.has_warnings:
                    raise PolicyError("Metadata transformation produced a warning.")
                parent = safe_parent(record.relative_parent) if options.organization == "source" else Path(".")
                if options.organization == "source" and len(directory_patients[record.relative_parent]) > 1:
                    # Preserve the source hierarchy, splitting only directories whose
                    # direct files belong to different patients. Use batch pseudonyms,
                    # never patient identifiers or names from the input paths.
                    parent /= pseudonym
                # Keep genuine copies, without assigning different instance UIDs or colliding paths.
                target = staging / parent / f"{ds.SOPInstanceUID}-{index:06d}.dcm"
                stage = "writing output"
                _write(ds, target)
                expected.append(ExpectedOutput(record, target, pseudonym, offset))
                if progress:
                    progress(Progress("De-identifying", index, scan.total))
        check_cancel(cancel)
        current_label = None
        stage = "validating output"
        report.findings.extend(validate_batch(
            scan, expected, uid_map, options=options, cancel=cancel, progress=progress
        ))
        if any(f.severity == "FAIL" for f in report.findings):
            return report
        def publish() -> bool:
            nonlocal stage, current_label
            current_label = None
            stage = "rechecking source files"
            if progress:
                progress(Progress("Rechecking source files", 0, 0))
            # Recheck on every attempt, including after a Windows lock wait.
            inventory_findings: list[Finding] = []
            final_paths = _paths(source, options.recursive, inventory_findings, cancel)
            if tuple(final_paths) != scan.source_files or inventory_findings:
                report.findings.append(Finding(
                    "FAIL", "SOURCE_CHANGED", "The input directory contents changed during processing."
                ))
                return False
            if progress:
                progress(Progress("Rechecking source files", 0, scan.total))
            for index, record in enumerate(scan.files, 1):
                check_cancel(cancel)
                current_label = record.file_id
                if file_digest(record.path) != record.sha256:
                    report.findings.append(Finding(
                        "FAIL", "SOURCE_CHANGED", "An input file changed before final validation.", record.file_id
                    ))
                    return False
                if progress:
                    progress(Progress("Rechecking source files", index, scan.total))
            current_label = None
            stage = "publishing output"
            if progress:
                progress(Progress("Publishing", 0, 0))
            check_directories(source, destination)
            check_cancel(cancel)
            if destination.exists():
                destination.rmdir()  # Empty only; newly created contents cause failure.
            staging.rename(destination)  # Same filesystem, directory-level atomic publication.
            return True

        if not retry_windows_file_lock(publish, cancel=cancel, retry_access_denied=True):
            return report
        staging = None
        report.status = "COMPLETED"
        report.output_count = len(expected)
        return report
    except Cancelled:
        report.status = "CANCELLED"
        report.findings.append(Finding("WARNING", "CANCELLED", "The run was cancelled; incomplete output was discarded."))
        return report
    except DirectoryError as error:
        report.findings.append(Finding("FAIL", "DIRECTORY_CONFIGURATION", str(error)))
        return report
    except PolicyError as error:
        report.findings.append(Finding("FAIL", "UNSUPPORTED_METADATA", str(error), current_label))
        return report
    except ReadError:
        report.findings.append(Finding(
            "FAIL", "DICOM_READ", "A DICOM file could not be read safely during processing.", current_label
        ))
        return report
    except Exception as error:
        report.findings.append(Finding(
            "FAIL", "PROCESSING_ERROR",
            f"Processing failed while {stage} ({safe_error_details(error)}). "
            "Check file access, other applications using the selected folders, "
            "and available disk space.", current_label,
        ))
        return report
    finally:
        if staging is not None:
            try:
                # Cleanup must still run after cancellation; retry temporary
                # Windows handles without changing permissions or publishing.
                retry_windows_file_lock(lambda: shutil.rmtree(staging), retry_access_denied=True)
            except OSError as error:
                report.findings.append(Finding(
                    "FAIL", "CLEANUP_FAILED",
                    "Incomplete output could not be removed "
                    f"({safe_error_details(error)}). Remove the hidden "
                    ".opendicom-incomplete folder beside the output before sharing."
                ))

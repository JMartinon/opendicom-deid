"""Offline CLI using the same engine and safe reports as the desktop."""

from __future__ import annotations

import argparse
import json
import signal
from pathlib import Path
from threading import Event

from . import __version__
from .core.models import Cancelled, Options


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="opendicom-deid",
        description="Offline DICOM de-identification. Privacy review is always required.",
    )
    result.add_argument("--version", action="version", version=f"OpenDICOM De-ID {__version__}")
    commands = result.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Count DICOM files and inspect the input dataset")
    scan.add_argument("input", type=Path)
    scan.add_argument("--no-recursive", action="store_true")
    process = commands.add_parser("deidentify", help="De-identify and validate a complete batch")
    process.add_argument("input", type=Path)
    process.add_argument("output", type=Path)
    process.add_argument("--no-recursive", action="store_true")
    process.add_argument(
        "--prefix", default="SUBJECT", help="Neutral pseudonym prefix; never a name or original ID"
    )
    process.add_argument("--dates", choices=["remove", "shift"], default="remove")
    process.add_argument(
        "--workers", type=int, choices=range(1, 9), default=1,
        help="Files to de-identify concurrently (1–8, default 1); more workers use more memory",
    )
    process.add_argument(
        "--text-policy", choices=["clinical", "strict"], default="clinical",
        help="Preserve screened RT names and descriptions (default), or scrub them; "
        "patient, clinician and institution identifiers are removed in both modes",
    )
    process.add_argument(
        "--organization", choices=["flat", "source"], default="source",
        help="Preserve nested source folders with neutral names (default), or use one flat folder",
    )
    process.add_argument(
        "--report", type=Path, help="Save a safe JSON report outside the input directory"
    )
    for command in (scan, process):
        command.add_argument(
            "--viewray-public-plan", action="store_true",
            help="Allow public-data research export of the recognized ViewRay private RT plan; "
            "vendor-private treatment fields are removed, full plan preservation is unsupported",
        )
    validate = commands.add_parser(
        "validate", help="Independently inspect a de-identified directory"
    )
    validate.add_argument("input", type=Path)
    validate.add_argument("--no-recursive", action="store_true")
    validate.add_argument("--report", type=Path)
    privacy = commands.add_parser(
        "inspect-privacy", help="Read-only screening for identifying fields; never privacy clearance"
    )
    privacy.add_argument("input", type=Path)
    privacy.add_argument("--file-id", required=True, type=int, help="File number from the scan report")
    privacy.add_argument("--no-recursive", action="store_true")
    return result


def report_path_allowed(path: Path, input_dir: Path) -> bool:
    """A report must not overwrite source files, or masquerade as a DICOM file."""
    try:
        return path.suffix.lower() == ".json" and not path.resolve().is_relative_to(
            input_dir.resolve()
        )
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "report", None) and not report_path_allowed(args.report, args.input):
        print("The report must be a .json file outside the input directory.")
        return 2
    cancel = Event()
    previous_handler = signal.getsignal(signal.SIGINT)
    signal.signal(
        signal.SIGINT,
        signal.default_int_handler if args.command == "validate" else lambda *_: cancel.set(),
    )
    try:
        if args.command == "inspect-privacy":
            from .core.privacy_inspection import inspect_privacy

            result = inspect_privacy(
                args.input, file_id=args.file_id, recursive=not args.no_recursive, cancel=cancel
            )
            print(json.dumps(result, indent=2))
            if result["status"] == "CANCELLED":
                return 130
            # Zero denotes completed inspection, never absence of identifying data.
            return 0 if result["status"] == "REVIEW_REQUIRED" else 1
        if args.command == "scan":
            from .core.scanner import scan_directory

            scan = scan_directory(
                args.input, recursive=not args.no_recursive, cancel=cancel,
                allow_viewray_public_plan=args.viewray_public_plan,
            )
            print(
                json.dumps(
                    {
                        "dicom_files": scan.total,
                        "modalities": scan.modalities,
                        "skipped_files": scan.skipped,
                        "findings": [
                            {
                                "severity": finding.severity,
                                "code": finding.code,
                                "message": finding.message,
                                "file_id": finding.file_id,
                            }
                            for finding in scan.findings
                        ],
                    },
                    indent=2,
                )
            )
            return 0 if scan.ok and scan.total else 1
        if args.command == "validate":
            from .core.validator import validate_directory

            report = validate_directory(args.input, recursive=not args.no_recursive)
        else:
            from .core.anonymizer import process_batch

            options = Options(
                recursive=not args.no_recursive,
                patient_prefix=args.prefix,
                date_mode=args.dates,
                organization=args.organization,
                allow_viewray_public_plan=args.viewray_public_plan,
                text_policy=args.text_policy,
                workers=args.workers,
            )
            report = process_batch(args.input, args.output, options, cancel=cancel)
        print(report.summary())
        if args.report:
            try:
                report.save(args.report)
            except FileExistsError:
                print(
                    "The report already exists. Choose a new filename; existing files are never overwritten."
                )
                return 1
            except OSError:
                print(
                    "The report could not be saved. Check the destination's permissions and free space."
                )
                return 1
        if report.status == "CANCELLED":
            return 130
        return 0 if report.ok else 1
    except (Cancelled, KeyboardInterrupt):
        print("Cancelled. No completed dataset was published.")
        return 130
    except ValueError:
        print(
            "Invalid settings. Use a neutral prefix of 1–16 letters, digits, underscores or hyphens, starting with a letter."
        )
        return 2
    except Exception:
        # Never print raw exception text: parsers and OS errors can contain PHI.
        print(
            "The operation failed. No success is claimed. Check the selected directories and inspect the data locally."
        )
        return 1
    finally:
        signal.signal(signal.SIGINT, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())

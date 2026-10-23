"""Bounded local workers for the transform pass of one transaction.

Only minimal tasks and the sealed batch mapping cross local process pipes. Source
records, snapshots and pixel buffers remain out of IPC; nothing is persisted or
sent over the network. Workers return fixed findings, never raw exceptions.
"""

from __future__ import annotations

import multiprocessing
import signal
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from .diagnostics import private_diagnostics
from .equipment_text import IdentifierToken
from .filesystem import safe_error_details
from .models import Cancelled, Finding, Options, Progress, ProgressCallback, check_cancel
from .policy import PolicyError, scrub_metadata
from .reader import ReadError, file_digest, read_dicom
from .reference_classes import apply_reference_class_repairs


@dataclass(frozen=True, repr=False)
class FileTask:
    """Sensitive in-memory task; never log or persist its contents."""

    source: Path
    target: Path
    sha256: str
    file_id: str
    pseudonym: str
    date_offset_days: int
    class_repairs: dict[str, str]


@dataclass(frozen=True, repr=False)
class WorkerState:
    uid_mapping: dict[str, str]
    identifiers: frozenset[IdentifierToken]
    options: Options


@dataclass(frozen=True, repr=False)
class WorkerResult:
    finding: Finding | None = None
    cancelled: bool = False


_state: WorkerState | None = None
_cancel: Any = None


def _initialize(state: WorkerState, cancel: Any) -> None:
    global _state, _cancel
    # The parent handles Ctrl+C and communicates cancellation through the event.
    # Avoid an uncaught KeyboardInterrupt traceback from a child reading a file.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _state, _cancel = state, cancel


def _transform(task: FileTask) -> WorkerResult:
    """Read and write exactly once, returning only non-identifying diagnostics."""
    stage = "checking source files"
    try:
        from .anonymizer import _reset_file_meta, _write

        if _state is None:
            raise RuntimeError()
        check_cancel(_cancel)
        if file_digest(task.source) != task.sha256:
            return WorkerResult(Finding(
                "FAIL", "SOURCE_CHANGED", "An input file changed after the scan.", task.file_id
            ))
        check_cancel(_cancel)
        stage = "reading source files"
        ds = read_dicom(task.source)
        if file_digest(task.source) != task.sha256:
            return WorkerResult(Finding(
                "FAIL", "SOURCE_CHANGED", "An input file changed during processing.", task.file_id
            ))
        check_cancel(_cancel)
        stage = "transforming metadata"
        with private_diagnostics() as diagnostics:
            apply_reference_class_repairs(ds, task.class_repairs)
            scrub_metadata(
                ds, pseudonym=task.pseudonym, uid_mapping=_state.uid_mapping,
                date_mode=_state.options.date_mode, date_offset_days=task.date_offset_days,
                known_identifiers=_state.identifiers, text_policy=_state.options.text_policy,
            )
            _reset_file_meta(ds)
        if diagnostics.has_warnings:
            raise PolicyError("Metadata transformation produced a warning.")
        check_cancel(_cancel)
        stage = "writing output"
        _write(ds, task.target)
        return WorkerResult()
    except Cancelled:
        return WorkerResult(cancelled=True)
    except PolicyError:
        return WorkerResult(Finding(
            "FAIL", "UNSUPPORTED_METADATA",
            "A metadata attribute could not be safely transformed.", task.file_id,
        ))
    except ReadError:
        return WorkerResult(Finding(
            "FAIL", "DICOM_READ", "A DICOM file could not be read safely during processing.",
            task.file_id,
        ))
    except BaseException as error:
        # Do not allow ProcessPoolExecutor to pickle exception objects or remote
        # tracebacks, which may include source paths and patient values.
        return WorkerResult(Finding(
            "FAIL", "PROCESSING_ERROR",
            f"Processing failed while {stage} ({safe_error_details(error)}). "
            "Check file access, other applications using the selected folders, "
            "and available disk space.", task.file_id,
        ))


def transform_parallel(
    tasks: list[FileTask], state: WorkerState, *, cancel: Event | None,
    progress: ProgressCallback | None,
) -> Finding | None:
    """Run at most N files at once and join every writer before returning.

    Cancellation/failure stops submission and signals active tasks. A file write
    already in progress may finish in staging; staging remains private and is
    discarded by the caller only after all worker processes have stopped.
    """
    if not tasks:
        return None
    context = multiprocessing.get_context("spawn")
    worker_cancel = context.Event()
    workers = min(state.options.workers, len(tasks))
    pool = ProcessPoolExecutor(
        max_workers=workers, mp_context=context, initializer=_initialize,
        initargs=(state, worker_cancel),
    )
    pending = {}
    next_task = 0
    completed = 0
    try:
        while next_task < len(tasks) or pending:
            check_cancel(cancel)
            while next_task < len(tasks) and len(pending) < workers:
                task = tasks[next_task]
                pending[pool.submit(_transform, task)] = next_task
                next_task += 1
            finished, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            check_cancel(cancel)
            # Stable order when several results become ready in the same poll.
            for future in sorted(finished, key=pending.__getitem__):
                task = tasks[pending.pop(future)]
                try:
                    result = future.result()
                except Exception:
                    # Covers process startup or unexpected worker termination;
                    # never replay the file or expose a remote exception.
                    return Finding(
                        "FAIL", "PROCESSING_ERROR",
                        "A parallel processing worker stopped unexpectedly. "
                        "No completed dataset was published.", task.file_id,
                    )
                if result.cancelled:
                    raise Cancelled()
                if result.finding is not None:
                    return result.finding
                completed += 1
                if progress:
                    progress(Progress("De-identifying", completed, len(tasks)))
        return None
    finally:
        worker_cancel.set()
        for future in pending:
            future.cancel()
        # Waiting is essential: no child may keep an open staging handle or
        # recreate an output after the caller starts transactional cleanup.
        pool.shutdown(wait=True, cancel_futures=True)

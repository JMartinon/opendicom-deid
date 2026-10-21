"""Shared public API. Source records are sensitive, in-memory objects; never serialize them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    from .inspection import Snapshot


@dataclass(frozen=True)
class Options:
    recursive: bool = True
    patient_prefix: str = "SUBJECT"
    date_mode: Literal["remove", "shift"] = "remove"
    organization: Literal["flat", "source"] = "source"
    allow_viewray_public_plan: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,15}", self.patient_prefix):
            raise ValueError("Use a neutral prefix: 1–16 letters, digits, underscores or hyphens.")
        if self.date_mode not in {"remove", "shift"}:
            raise ValueError("Date mode must be remove or shift.")
        if self.organization not in {"flat", "source"}:
            raise ValueError("Organization must be flat or source.")
        if type(self.allow_viewray_public_plan) is not bool:
            raise ValueError("The ViewRay public-data export option must be a boolean.")


@dataclass(frozen=True)
class Progress:
    phase: str
    completed: int
    total: int


ProgressCallback = Callable[[Progress], None]


class Cancelled(Exception):
    """Cooperative cancellation; no exception text contains source data."""


def check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


@dataclass(frozen=True)
class Finding:
    severity: Literal["PASS", "WARNING", "FAIL"]
    code: str
    message: str
    file_id: str | None = None


@dataclass(repr=False)
class FileRecord:
    path: Path
    file_id: str
    relative_parent: Path
    sha256: str
    snapshot: Snapshot
    patient_key: tuple[str, ...]
    modality: str


@dataclass(repr=False)
class ScanResult:
    input_dir: Path
    files: list[FileRecord] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    modalities: dict[str, int] = field(default_factory=dict)
    skipped: int = 0
    source_files: tuple[Path, ...] = ()

    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "FAIL" for f in self.findings)


@dataclass(repr=False)
class ExpectedOutput:
    source: FileRecord
    path: Path
    pseudonym: str
    date_offset_days: int

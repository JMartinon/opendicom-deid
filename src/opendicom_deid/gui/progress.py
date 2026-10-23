"""Thread-safe latest progress and patient-free labels for the desktop view."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from ..core.models import Progress


class LatestProgress:
    """Keep one current update, so fast workers cannot create a stale UI backlog."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._value: Progress | None = None
        self._active = False

    def begin(self) -> None:
        with self._lock:
            self._value = None
            self._active = True

    def finish(self) -> None:
        """Discard a final update that raced with delivery of the completed result."""
        with self._lock:
            self._active = False
            self._value = None

    def publish(self, value: Progress) -> None:
        with self._lock:
            if self._active:
                self._value = value

    def take(self) -> Progress | None:
        with self._lock:
            value, self._value = self._value, None
            return value


@dataclass(frozen=True)
class ProgressDisplay:
    percentage: float | None
    label: str


def progress_display(value: Progress) -> ProgressDisplay:
    """Show a file percentage for the current stage, never a time estimate."""
    phase = {
        "scan": "Scanning",
        "scanning": "Scanning",
        "anonymize": "De-identifying",
        "anonymizing": "De-identifying",
        "de-identifying": "De-identifying",
        "validate": "Validating",
        "validating": "Validating",
        "rechecking source files": "Rechecking source files",
    }.get(value.phase.lower(), "Processing")
    if value.total <= 0:
        label = {
            "inventory": "Finding input files…",
            "rechecking source files": "Rechecking the input directory…",
            "checking references": "Checking DICOM references…",
            "publishing": "Publishing the completed dataset…",
        }.get(value.phase.lower(), f"{phase}…")
        return ProgressDisplay(None, label)
    completed = min(max(0, value.completed), value.total)
    percentage = 100 * completed / value.total
    # Floor the text percentage: 100% means every file in this stage is done.
    return ProgressDisplay(
        percentage,
        f"{phase}: {completed} / {value.total} files · {int(percentage)}% of this stage",
    )

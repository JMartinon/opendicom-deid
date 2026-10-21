"""Reports contain only fixed messages, counts, and neutral per-file labels."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from opendicom_deid import __version__

from .models import Finding


@dataclass
class Report:
    status: Literal["COMPLETED", "FAILED", "CANCELLED"] = "FAILED"
    input_count: int = 0
    output_count: int = 0
    modalities: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    privacy_review: str = "REQUIRED"
    version: str = __version__

    @property
    def ok(self) -> bool:
        return self.status == "COMPLETED" and not any(
            f.severity == "FAIL" for f in self.findings
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        """Explicit user-requested export. Does not include paths, UIDs or patient values."""
        with Path(path).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(self.to_dict(), indent=2) + "\n")

    def summary(self) -> str:
        counts = Counter(f.severity for f in self.findings)
        state = {
            "COMPLETED": "Technical processing completed",
            "FAILED": "Processing failed — no completed dataset was published",
            "CANCELLED": "Processing cancelled — no completed dataset was published",
        }[self.status]
        lines = [state, f"Files processed: {self.output_count} / {self.input_count}"]
        lines.extend(f"{name}: {count}" for name, count in sorted(self.modalities.items()))
        lines.append(
            f"Checks: {counts['PASS']} passed, {counts['WARNING']} warnings, {counts['FAIL']} failed"
        )
        lines.extend(f"{f.severity}: {f.message}" for f in self.findings)
        lines.extend([
            "Privacy review: REQUIRED",
            "Pixels are unchanged. Review burned-in text, recognizable anatomy, free text, "
            "and vendor-specific content before sharing.",
        ])
        return "\n".join(lines)

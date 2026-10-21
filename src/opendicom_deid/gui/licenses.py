"""Read bundled or installed license texts without network access."""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path


def license_text() -> str:
    frozen_root = getattr(sys, "_MEIPASS", None)
    paths: list[tuple[str, Path]] = []
    if frozen_root:
        base = Path(frozen_root) / "licenses"
        paths.extend(
            (str(path.relative_to(base)), path)
            for path in sorted(base.rglob("*"))
            if path.is_file()
        )
    else:
        root = Path(__file__).resolve().parents[3]
        if (root / "LICENSE").is_file():
            paths.append(("OpenDICOM De-ID", root / "LICENSE"))
        notices = root / "THIRD_PARTY_LICENSES"
        paths.extend((path.name, path) for path in sorted(notices.glob("*")) if path.is_file())
        for name in ("opendicom-deid", "pydicom", "dicom-anonymizer", "tqdm"):
            try:
                dist = metadata.distribution(name)
            except metadata.PackageNotFoundError:
                continue
            for item in dist.files or []:
                if any(part.endswith(".dist-info") for part in item.parts) and (
                    "licenses" in item.parts
                    or item.name.lower().startswith(("license", "licence", "copying", "notice"))
                ):
                    path = Path(dist.locate_file(item))
                    if path.is_file():
                        paths.append((f"{name} {dist.version} — {item.name}", path))
    sections = []
    for name, path in paths:
        try:
            sections.append(
                f"{name}\n{'=' * 60}\n{path.read_text(encoding='utf-8', errors='replace')}"
            )
        except OSError:
            sections.append(f"{name}\nLicense notice could not be read.")
    return (
        "\n\n".join(sections)
        or "License notices are available in the application's distribution archive."
    )

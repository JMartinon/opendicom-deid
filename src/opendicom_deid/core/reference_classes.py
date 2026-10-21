"""Check reference class labels and narrowly repair a resolved ViewRay plan link.

An instance UID selects the target. Class similarity, modality, filenames, or
the presence of just one plan never select a replacement target. Original class
and instance identifiers remain in memory; diagnostics expose neutral labels.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from pydicom.dataset import Dataset
from pydicom.uid import RTPlanStorage, UID_dictionary

from .inspection import Snapshot, uid_values, walk_elements
from .models import FileRecord, Finding
from .sop_classes import KNOWN_PRIVATE_STORAGE_CLASSES, VIEWRAY_RT_PLAN_STORAGE


def reference_class_repairs(
    records: Sequence[FileRecord], *, enabled: bool,
) -> dict[str, dict[str, str]]:
    """Derive authorized corrections solely from the immutable source inventory."""
    repairs: dict[str, dict[str, str]] = {}
    if not enabled:
        return repairs
    instances: dict[str, list[FileRecord]] = defaultdict(list)
    for record in records:
        instances[record.snapshot.sop_uid].append(record)
    for record in records:
        for path, references in record.snapshot.references.items():
            if not path.endswith("/00081155") or len(references) != 1:
                continue
            class_path = path[:-8] + "00081150"
            if record.snapshot.technical_uids.get(class_path) != (str(RTPlanStorage),):
                continue
            targets = instances.get(references[0], [])
            if not targets or len({target.sha256 for target in targets}) != 1:
                continue
            if all(
                target.snapshot.sop_class_uid == VIEWRAY_RT_PLAN_STORAGE
                and target.modality == "RTPLAN"
                and target.patient_key == record.patient_key
                for target in targets
            ):
                repairs.setdefault(record.file_id, {})[class_path] = VIEWRAY_RT_PLAN_STORAGE
    return repairs


def _class_label(classes: tuple[str, ...]) -> str:
    if len(classes) != 1:
        return "a missing or multivalued class"
    value = classes[0]
    if value in KNOWN_PRIVATE_STORAGE_CLASSES:
        return KNOWN_PRIVATE_STORAGE_CLASSES[value]
    definition = UID_dictionary.get(value)
    if definition and definition[1] == "SOP Class":
        return definition[0]
    return "an unrecognized or private class"


def reference_class_findings(
    snapshots: Sequence[tuple[str, Snapshot]],
    repairs: Mapping[str, Mapping[str, str]] | None = None,
) -> list[Finding]:
    """Report resolved class mismatches without source UIDs, values, or filenames."""
    instances: dict[str, list[tuple[str, Snapshot]]] = defaultdict(list)
    for label, snapshot in snapshots:
        instances[snapshot.sop_uid].append((label, snapshot))
    findings: list[Finding] = []
    for label, snapshot in snapshots:
        for path, references in snapshot.references.items():
            if not path.endswith("/00081155"):
                continue
            class_path = path[:-8] + "00081150"
            classes = snapshot.technical_uids.get(class_path, ())
            correction = (repairs or {}).get(label, {}).get(class_path)
            if correction:
                findings.append(Finding(
                    "WARNING", "VIEWRAY_REFERENCE_CLASS_REPAIR",
                    f"Reference {class_path} names standard RT Plan Storage but resolves to the "
                    "selected same-patient ViewRay private plan. Research export aligns this "
                    "reference's class label with its target; the plan class is unchanged. "
                    "Review downstream compatibility.", label,
                ))
                classes = (correction,)
            for reference in references:
                for target_label, target in instances.get(reference, []):
                    if classes and target.sop_class_uid not in classes:
                        findings.append(Finding(
                            "FAIL", "REFERENCE_CLASS_MISMATCH",
                            f"Reference {class_path} declares {_class_label(classes)}, but target "
                            f"{target_label} uses {_class_label((target.sop_class_uid,))}. "
                            "This class mismatch requires review.", label,
                        ))
                        break
    return findings


def apply_reference_class_repairs(ds: Dataset, repairs: Mapping[str, str]) -> None:
    """Change only previously resolved reference-class paths before UID remapping."""
    if not repairs:
        return
    remaining = set(repairs)
    for path, element in walk_elements(ds):
        if path not in repairs:
            continue
        if element.keyword != "ReferencedSOPClassUID" or uid_values(element) != (str(RTPlanStorage),):
            raise ValueError("A source reference class no longer matches its planned correction.")
        if repairs[path] != VIEWRAY_RT_PLAN_STORAGE:
            raise ValueError("An unsupported reference class correction was requested.")
        element.value = VIEWRAY_RT_PLAN_STORAGE
        remaining.remove(path)
    if remaining:
        raise ValueError("A source reference class is missing from its planned correction.")

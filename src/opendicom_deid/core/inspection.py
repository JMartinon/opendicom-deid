"""In-memory source fingerprints used by an independent output verifier.

Snapshots contain source identifiers and must never be serialized or logged.
All paths use DICOM tags and sequence indices, never patient-derived strings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterator

from pydicom.dataelem import DataElement
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue


@dataclass(frozen=True, repr=False)
class Snapshot:
    sop_uid: str
    sop_class_uid: str
    study_uid: str
    series_uid: str
    frame_uids: tuple[str, ...]
    patient_id: str
    patient_name: str
    entity_uids: dict[str, tuple[str, ...]]
    technical_uids: dict[str, tuple[str, ...]]
    references: dict[str, tuple[str, ...]]
    clinical: dict[str, str]
    semantic_digest: str
    private_count: int
    dates: dict[str, tuple[str, tuple[str, ...]]]
    transfer_syntax_uid: str


def walk_elements(ds: Dataset, prefix: str = "") -> Iterator[tuple[str, DataElement]]:
    """Visit every element, including arbitrarily nested sequence items."""
    for elem in ds:
        path = f"{prefix}/{int(elem.tag):08X}"
        yield path, elem
        if elem.VR == "SQ":
            for index, item in enumerate(elem.value):
                yield from walk_elements(item, f"{path}[{index}]")


def uid_values(elem: DataElement) -> tuple[str, ...]:
    values = elem.value if isinstance(elem.value, (list, tuple, MultiValue)) else [elem.value]
    return tuple(str(value) for value in values if value is not None and str(value))


_NUMERIC_VRS = {"DS", "FD", "FL", "IS", "SL", "SS", "UL", "US", "SV", "UV"}
_CLINICAL_GROUPS = {0x0018, 0x0020, 0x0028, 0x3002, 0x3004, 0x3006, 0x3008, 0x300A}
_CLINICAL_BINARY_TAGS = {
    0x7FE00001, 0x7FE00002, 0x7FE00003,  # extended frame offsets, lengths and total length
    0x7FE00010, 0x7FE00008, 0x7FE00009,  # native, encapsulated and floating point pixels
    0x56000010, 0x56000020,  # spectroscopy phase correction and spectroscopy payload
}
_CLINICAL_TEXT = {
    "Modality", "PhotometricInterpretation", "RescaleType", "DoseUnits", "DoseType",
    "DoseSummationType", "RTROIInterpretedType", "ContourGeometricType",
    "BeamType", "RadiationType", "TreatmentDeliveryType", "RTBeamLimitingDeviceType",
    "PatientPosition", "PixelRepresentation",
}
_INSTANCE_REFERENCE_KEYWORDS = {
    "ReferencedSOPInstanceUID", "ReferencedSOPInstanceUIDInFile",
    "MultiFrameSourceSOPInstanceUID", "SOPInstanceUIDOfConcatenationSource",
    "ReferencedColorPaletteInstanceUID",
}


def _normalized_value(elem: DataElement) -> str:
    if isinstance(elem.value, bytes):
        return "sha256:" + hashlib.sha256(elem.value).hexdigest()
    if elem.VR == "SQ":
        return str(len(elem.value))
    values = elem.value if isinstance(elem.value, (list, tuple, MultiValue)) else [elem.value]
    if elem.VR in _NUMERIC_VRS:
        normalized = []
        for value in values:
            try:
                number = Decimal(str(value))
                normalized.append(str(number.normalize()) if number else "0")
            except (InvalidOperation, ValueError):
                normalized.append(str(value))
        return json.dumps(normalized, separators=(",", ":"))
    return json.dumps([str(value) for value in values], separators=(",", ":"))


def capture_snapshot(ds: Dataset) -> Snapshot:
    """Capture identifiers, selected clinical values and an exact semantic digest."""
    from .policy import is_removed_container, should_remove_sequence
    from .uid_mapper import uid_kind

    entities: dict[str, tuple[str, ...]] = {}
    technical: dict[str, tuple[str, ...]] = {}
    references: dict[str, tuple[str, ...]] = {}
    clinical: dict[str, str] = {}
    dates: dict[str, tuple[str, tuple[str, ...]]] = {}
    frames: set[str] = set()
    digest = hashlib.sha256()
    private_count = 0
    excluded_prefixes: list[str] = []
    for path, elem in walk_elements(ds):
        private_count += int(elem.tag.is_private)
        normalized = _normalized_value(elem)
        digest.update(json.dumps((path, elem.VR, normalized)).encode("utf-8"))
        if any(path.startswith(prefix) for prefix in excluded_prefixes):
            continue
        if elem.tag.is_private or is_removed_container(elem) or (
            elem.VR == "SQ" and should_remove_sequence(elem)
        ):
            excluded_prefixes.append(path + "[")
            continue
        if elem.VR in {"DA", "DT", "TM"}:
            dates[path] = (elem.VR, uid_values(elem))
        if elem.VR == "UI":
            kind = uid_kind(elem)
            if kind == "entity":
                entities[path] = uid_values(elem)
            elif kind == "technical":
                technical[path] = uid_values(elem)
            if "FrameOfReferenceUID" in elem.keyword:
                frames.update(uid_values(elem))
            if elem.keyword in _INSTANCE_REFERENCE_KEYWORDS:
                references[path] = uid_values(elem)
        if (
            (elem.tag.group in _CLINICAL_GROUPS and elem.VR in _NUMERIC_VRS)
            or int(elem.tag) in _CLINICAL_BINARY_TAGS
            or elem.keyword in _CLINICAL_TEXT
        ):
            clinical[path] = normalized
    return Snapshot(
        sop_uid=str(ds.get("SOPInstanceUID", "")),
        sop_class_uid=str(ds.get("SOPClassUID", "")),
        study_uid=str(ds.get("StudyInstanceUID", "")),
        series_uid=str(ds.get("SeriesInstanceUID", "")),
        frame_uids=tuple(sorted(frames)),
        patient_id=str(ds.get("PatientID", "")),
        patient_name=str(ds.get("PatientName", "")),
        entity_uids=entities,
        technical_uids=technical,
        references=references,
        clinical=clinical,
        semantic_digest=digest.hexdigest(),
        private_count=private_count,
        dates=dates,
        transfer_syntax_uid=str(getattr(ds, "file_meta", {}).get("TransferSyntaxUID", "")),
    )

"""Explicit equipment text retention and neutral beam labels.

Known-identifier screening is bounded, not a claim that arbitrary local machine
names are anonymous. Original values and identifier tokens stay in memory only.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator

from pydicom.dataelem import DataElement
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue

from .uid_mapper import uid_kind

# Normalized value and whether a short numeric StudyID requires a whole-field
# match. Strong patient identifiers must still match delimited short components.
IdentifierToken = tuple[str, bool]

EQUIPMENT_TEXT_KEYWORDS = frozenset({
    "Manufacturer", "ManufacturerModelName", "SoftwareVersions",
    "TreatmentMachineName", "FluenceModeID", "MaterialID",
})
_IDENTIFIER_KEYWORDS = frozenset({
    "PatientID", "OtherPatientIDs", "StudyID", "AccessionNumber", "AdmissionID",
    "RequestedProcedureID", "MedicalRecordLocator", "PatientBirthDate",
    "PatientAddress", "PatientTelephoneNumbers", "PatientTelecomInformation",
    "ClinicalTrialSubjectID", "ClinicalTrialSubjectReadingID",
})


def text_values(element: DataElement) -> tuple[str, ...]:
    value = element.value
    values = value if isinstance(value, (MultiValue, list, tuple)) else [value]
    return tuple(str(value) if value is not None else "" for value in values) or ("",)


def _normalized(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value).casefold() if char.isalnum()
    )


def _public_elements(ds: Dataset, prefix: str = "") -> Iterator[tuple[str, Dataset, DataElement]]:
    for tag in ds.keys():
        # Avoid converting malformed private values, even for in-memory callers.
        if tag.is_private:
            continue
        element = ds[tag]
        path = f"{prefix}/{int(tag):08X}"
        yield path, ds, element
        if element.VR == "SQ":
            for index, child in enumerate(element.value):
                yield from _public_elements(child, f"{path}[{index}]")


def identifier_tokens(ds: Dataset) -> frozenset[IdentifierToken]:
    """Collect known identifiers before mutation, including nested and historical ones."""
    tokens: set[IdentifierToken] = set()
    for _, _, element in _public_elements(ds):
        if element.VR == "PN" or element.keyword in _IDENTIFIER_KEYWORDS or (
            element.VR == "UI" and uid_kind(element) == "entity"
        ):
            for value in text_values(element):
                normalized = _normalized(value)
                if normalized:
                    tokens.add((normalized, element.keyword == "StudyID" and normalized.isdecimal()))
                if element.VR == "PN":
                    # All alphabetic/ideographic/phonetic representations and
                    # name components, including reversed order and separators.
                    tokens.update(
                        (part, False) for raw in re.split(r"[\W_]+", value)
                        if (part := _normalized(raw))
                    )
    return frozenset(tokens)


def contains_identifier(value: str, tokens: Iterable[IdentifierToken]) -> bool:
    normalized = _normalized(value)
    words = {_normalized(part) for part in re.split(r"[\W_]+", value)}
    for token, short_exact_only in tokens:
        if len(token) >= 3 and token in normalized:
            return True
        # StudyID=1 should not match version 5.5.4.14. Strong short identifiers
        # such as PatientID=42 must match a delimited machine name like LINAC-42.
        if token == normalized or (not short_exact_only and token in words):
            return True
    return False


def cleaned_equipment_values(
    values: tuple[str, ...], tokens: Iterable[IdentifierToken],
) -> tuple[str, ...]:
    return tuple("ANONYMIZED" if contains_identifier(value, tokens) else value for value in values)


def neutral_beam_name(item: Dataset) -> str:
    """Keep beam identity through its number, never through a user-entered name."""
    number = str(item.get("BeamNumber", item.get("ReferencedBeamNumber", ""))).strip()
    if not re.fullmatch(r"\+?[0-9]{1,12}", number):
        return "ANONYMIZED"
    number = str(int(number))
    label = f"BEAM-{number}"
    return label if len(label) <= 16 else f"B{number}"


def capture_equipment_text(ds: Dataset) -> tuple[
    dict[str, tuple[str, ...]], dict[str, tuple[str, ...]], dict[str, tuple[str, ...]],
]:
    """Capture retained paths and expected beam labels, excluding removed containers."""
    from .policy import is_removed_container, should_remove_sequence

    equipment: dict[str, tuple[str, ...]] = {}
    beams: dict[str, tuple[str, ...]] = {}
    neutral_beams: dict[str, tuple[str, ...]] = {}
    excluded: list[str] = []
    for path, item, element in _public_elements(ds):
        if any(path.startswith(prefix) for prefix in excluded):
            continue
        if is_removed_container(element) or (
            element.VR == "SQ" and should_remove_sequence(element)
        ):
            excluded.append(path + "[")
            continue
        if element.keyword in EQUIPMENT_TEXT_KEYWORDS:
            equipment[path] = text_values(element)
        elif element.keyword == "BeamName":
            beams[path] = text_values(element)
            neutral_beams[path] = (neutral_beam_name(item) if element.value else "",)
    return equipment, beams, neutral_beams

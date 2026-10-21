"""Conservative metadata policy, inspired by PS3.15 but not a conformance claim.

Only Kitware's published rule *data* is used.  We deliberately own traversal and
mutation: calling its top-level anonymizer can miss ordinary tags inside items,
and recursively emptying a reference sequence can destroy clinical relationships.
See docs/confidentiality.md for the policy's deliberate limits and exceptions.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, timedelta
from functools import lru_cache
from typing import Literal

from dicomanonymizer.dicom_anonymization_databases import dicomfields_2024b
from pydicom.dataelem import DataElement
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue
from pydicom.tag import BaseTag, Tag

from .uid_mapper import uid_kind

DateMode = Literal["remove", "shift"]
POLICY_NAME = "OpenDICOM De-ID metadata policy v1"
RULE_BASELINE = "Kitware dicom-anonymizer 2.1.0 / DICOM 2024b tables"

# Code meanings and units carry clinical semantics. They are an explicit review
# boundary: local codes and misleading meanings can still identify a patient.
PRESERVED_TEXT_KEYWORDS = frozenset(
    {
        "CodeValue",
        "LongCodeValue",
        "URNCodeValue",
        "CodingSchemeDesignator",
        "CodingSchemeVersion",
        "CodeMeaning",
        "RescaleType",
    }
)
TEXT_VRS = frozenset({"AE", "LO", "LT", "PN", "SH", "ST", "UC", "UT", "UR"})

# Never retain these containers, even if historical metadata contains references.
# Their removal is intentional and must be excluded from the live object graph.
REMOVED_CONTAINER_KEYWORDS = frozenset(
    {
        "OriginalAttributesSequence",
        "ModifiedAttributesSequence",
        "NonconformingModifiedAttributesSequence",
        "EncryptedAttributesSequence",
        "DigitalSignaturesSequence",
        "MACParametersSequence",
        "IconImageSequence",
        "GraphicAnnotationSequence",
        "DeidentificationMethodCodeSequence",
    }
)

_RULE_NAMES = (
    "D_TAGS", "Z_TAGS", "X_TAGS", "U_TAGS", "Z_D_TAGS", "X_Z_TAGS",
    "X_D_TAGS", "X_Z_D_TAGS", "X_Z_U_STAR_TAGS",
)
_EXACT_RULES: dict[BaseTag, str] = {}
_MASK_RULES: list[tuple[tuple[int, ...], str]] = []
for _rule_name in _RULE_NAMES:
    for _tag_spec in getattr(dicomfields_2024b, _rule_name):
        if len(_tag_spec) == 2:
            _EXACT_RULES[Tag(_tag_spec)] = _rule_name
        else:
            _MASK_RULES.append((tuple(_tag_spec), _rule_name))


class PolicyError(ValueError):
    """Metadata cannot safely be transformed; messages never contain values."""


@lru_cache(maxsize=4096)
def rule_for_tag(tag: BaseTag) -> str | None:
    """Return the Kitware category, including repeating-group rules."""
    if tag in _EXACT_RULES:
        return _EXACT_RULES[tag]
    for spec, rule in _MASK_RULES:
        group, element, group_mask, element_mask = spec
        if (tag.group & group_mask) == (group & group_mask) and (
            tag.element & element_mask
        ) == (element & element_mask):
            return rule
    return None


def is_removed_container(element: DataElement) -> bool:
    """Identify metadata containers whose references are intentionally discarded."""
    return (
        element.keyword in REMOVED_CONTAINER_KEYWORDS
        or element.tag.group in {0x0000, 0x0002, 0x0004, 0x0400}
        or 0x5000 <= element.tag.group <= 0x50FF
        or 0x6000 <= element.tag.group <= 0x60FF
    )


def _contains_entity_uid(dataset: Dataset) -> bool:
    for tag in list(dataset.keys()):
        if tag.is_private:
            continue
        element = dataset[tag]
        if is_removed_container(element):
            continue
        if element.VR == "UI" and uid_kind(element) == "entity":
            return True
        if element.VR == "SQ" and any(_contains_entity_uid(item) for item in element.value):
            return True
    return False


def should_remove_sequence(element: DataElement) -> bool:
    """Whether the original sequence is an intentionally removed container.

    Validation uses this same documented boundary to distinguish removed history
    from relationships that the transformed dataset must preserve.
    """
    return is_removed_container(element) or (
        element.VR == "SQ"
        and rule_for_tag(element.tag) is not None
        and not any(_contains_entity_uid(child) for child in element.value)
    )


def _values(element: DataElement) -> list[str]:
    value = element.value
    if isinstance(value, (MultiValue, list, tuple)):
        return [str(item) if item is not None else "" for item in value]
    return [str(value) if value is not None else ""]


def _replace_values(element: DataElement, values: list[str]) -> None:
    element.value = values if len(values) > 1 else values[0]


_DT_PATTERN = re.compile(r"[0-9]{8}(?:[0-9]{2}(?:[0-9]{2}(?:[0-9]{2}(?:\.[0-9]{1,6})?)?)?)?(?:[+-][0-9]{4})?")
_TM_PATTERN = re.compile(r"[0-9]{2}(?:[0-9]{2}(?:[0-9]{2}(?:\.[0-9]{1,6})?)?)?")


def _valid_clock(value: str) -> bool:
    clock = value.split(".", 1)[0]
    return (
        int(clock[:2]) <= 23
        and (len(clock) < 4 or int(clock[2:4]) <= 59)
        and (len(clock) < 6 or int(clock[4:6]) <= 60)
    )


def _valid_timezone(value: str) -> bool:
    if not re.fullmatch(r"[+-][0-9]{4}", value):
        return False
    maximum = 1400 if value[0] == "+" else 1200
    return int(value[1:]) <= maximum and int(value[-2:]) < 60


def _shift_date(value: str, vr: str, offset: int) -> str:
    if not value:
        return ""
    if vr == "TM":
        if not _TM_PATTERN.fullmatch(value) or not _valid_clock(value):
            raise PolicyError("A time value cannot safely be retained.")
        return value
    if vr == "DA":
        valid = bool(re.fullmatch(r"[0-9]{8}", value))
    else:
        valid = bool(_DT_PATTERN.fullmatch(value))
        clock = re.split(r"[+-]", value[8:], maxsplit=1)[0]
        valid = valid and (not clock or _valid_clock(clock))
        if re.search(r"[+-][0-9]{4}$", value):
            valid = valid and _valid_timezone(value[-5:])
    if not valid:
        raise PolicyError("A partial or malformed date cannot safely be shifted.")
    try:
        shifted = date(int(value[:4]), int(value[4:6]), int(value[6:8])) + timedelta(days=offset)
    except (ValueError, OverflowError):
        raise PolicyError("A date cannot safely be shifted.") from None
    return f"{shifted.year:04d}{shifted.month:02d}{shifted.day:02d}{value[8:]}"


def scrub_metadata(
    dataset: Dataset,
    *,
    pseudonym: str,
    uid_mapping: Mapping[str, str],
    date_mode: DateMode = "remove",
    date_offset_days: int = 0,
) -> None:
    """Mutate metadata recursively without touching pixel or geometry values.

    ``uid_mapping`` must already include every retained entity and reference UID.
    No new mapping is generated here. File meta and preamble are rewritten by the
    caller. The reader should strip raw private tags before triggering conversion;
    the key-first deletion here also protects callers using an in-memory Dataset.
    This is a single-use transform on original metadata, not an idempotent filter.
    """
    if date_mode not in {"remove", "shift"}:
        raise PolicyError("Unsupported date policy.")
    if date_mode == "shift" and (type(date_offset_days) is not int or not date_offset_days):
        raise PolicyError("Date shifting requires a nonzero whole-day offset.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", pseudonym):
        raise PolicyError("The patient pseudonym is not a valid neutral identifier.")

    def scrub(item: Dataset) -> None:
        for tag in list(item.keys()):
            # Inspect the tag itself before materializing its raw value. This is
            # essential for malformed private FD values such as ViewRay 01F1,1026.
            if tag.is_private:
                del item[tag]
                continue
            element = item[tag]
            if is_removed_container(element):
                del item[tag]
                continue
            keyword = element.keyword
            rule = rule_for_tag(tag)
            if keyword in {"PatientName", "PatientID"}:
                element.value = pseudonym
                continue
            if element.VR == "SQ":
                # Required reference containers must survive all X/Z/U* and
                # other table actions. Traverse their items instead of blanking
                # every value, which would also erase numbers and class UIDs.
                if should_remove_sequence(element):
                    del item[tag]
                    continue
                for child in element.value:
                    scrub(child)
                continue
            if element.VR == "UI":
                kind = uid_kind(element)
                if kind == "remove":
                    del item[tag]
                elif kind == "entity":
                    try:
                        mapped = [uid_mapping[value] if value else "" for value in _values(element)]
                    except KeyError:
                        raise PolicyError("An entity UID was not included in the batch mapping.") from None
                    _replace_values(element, mapped)
                elif kind != "technical":
                    raise PolicyError("An attribute has an unsupported UID meaning.")
                continue
            if element.VR in {"DA", "DT", "TM"}:
                replacement = (
                    [_shift_date(value, element.VR, date_offset_days) for value in _values(element)]
                    if date_mode == "shift" and keyword not in {"PatientBirthDate", "PatientBirthTime"}
                    else [""]
                )
                _replace_values(element, replacement)
                continue
            if keyword == "TimezoneOffsetFromUTC" and date_mode == "shift":
                # A whole-day shift preserves the date/time offset relationship.
                if element.value and not _valid_timezone(str(element.value)):
                    raise PolicyError("The timezone offset is malformed.")
                continue
            if rule == "X_TAGS":
                del item[tag]
            elif element.VR in TEXT_VRS and keyword not in PRESERVED_TEXT_KEYWORDS:
                element.value = "ANONYMIZED" if element.value else ""
            elif element.VR == "AS":
                element.value = ""
            elif rule and rule != "U_TAGS":
                if element.VR == "CS":
                    element.value = "ANONYMIZED" if rule in {"D_TAGS", "X_D_TAGS", "Z_D_TAGS", "X_Z_D_TAGS"} else ""
                elif element.VR in {"OB", "OW", "OF", "OD", "OL", "OV", "UN"}:
                    raise PolicyError("An opaque identifying attribute cannot safely be transformed.")
                else:
                    # No current baseline rule requires overwriting a clinical
                    # numeric value. Fail closed if a future rule adds one.
                    raise PolicyError("An unsupported metadata rule requires review.")
            elif element.VR == "UN":
                raise PolicyError("An unknown-value attribute requires review.")

    scrub(dataset)
    dataset.PatientName = pseudonym
    dataset.PatientID = pseudonym
    dataset.PatientIdentityRemoved = "YES"
    dataset.DeidentificationMethod = [
        f"{POLICY_NAME}; no profile conformance claim",
        "Pixel data unchanged; privacy review required",
    ]
    dataset.LongitudinalTemporalInformationModified = "MODIFIED" if date_mode == "shift" else "REMOVED"


def metadata_issues(
    dataset: Dataset,
    *,
    pseudonym: str | None = None,
    date_mode: DateMode = "remove",
) -> set[str]:
    """Check known residual policy violations without exposing input values.

    This checks metadata policy, not proof of privacy: preserved codes, pixels,
    unexpected content in ordinary numeric/code-string VRs, and recognizable
    anatomy still need human assessment. UID graph checks are independent.
    """
    issues: set[str] = set()

    def inspect(item: Dataset) -> None:
        for tag in item.keys():
            if tag.is_private:
                issues.add("private_attributes")
                continue
            element = item[tag]
            keyword = element.keyword
            if is_removed_container(element):
                issues.add("identifying_containers")
                continue
            if element.VR == "SQ":
                if should_remove_sequence(element):
                    issues.add("identifying_containers")
                for child in element.value:
                    inspect(child)
                continue
            if keyword in {"PatientName", "PatientID"}:
                if pseudonym is not None and str(element.value) != pseudonym:
                    issues.add("patient_pseudonym")
                continue
            if keyword == "DeidentificationMethod":
                if _values(element) != [
                    f"{POLICY_NAME}; no profile conformance claim",
                    "Pixel data unchanged; privacy review required",
                ]:
                    issues.add("method_marker")
                continue
            if element.VR == "UN":
                issues.add("opaque_attributes")
            elif element.VR == "UI":
                if uid_kind(element) not in {"entity", "technical"}:
                    issues.add("unsupported_uids")
            elif element.VR in {"DA", "DT", "TM"}:
                if date_mode == "remove" or keyword in {"PatientBirthDate", "PatientBirthTime"}:
                    if any(_values(element)):
                        issues.add("identifying_dates")
                else:
                    try:
                        for value in _values(element):
                            _shift_date(value, element.VR, 0)
                    except PolicyError:
                        issues.add("invalid_dates")
            elif keyword == "TimezoneOffsetFromUTC" and date_mode == "shift":
                if element.value and not _valid_timezone(str(element.value)):
                    issues.add("invalid_dates")
                continue
            elif rule_for_tag(tag) == "X_TAGS":
                issues.add("identifying_attributes")
            elif element.VR in TEXT_VRS and keyword not in PRESERVED_TEXT_KEYWORDS:
                if any(value not in {"", "ANONYMIZED"} for value in _values(element)):
                    issues.add("identifying_text")
            elif element.VR == "AS" and element.value:
                issues.add("identifying_attributes")
            elif element.VR == "CS" and rule_for_tag(tag):
                if any(value not in {"", "ANONYMIZED"} for value in _values(element)):
                    issues.add("identifying_attributes")

    inspect(dataset)
    if getattr(dataset, "PatientIdentityRemoved", "") != "YES":
        issues.add("identity_marker")
    expected_temporal = "MODIFIED" if date_mode == "shift" else "REMOVED"
    if getattr(dataset, "LongitudinalTemporalInformationModified", "") != expected_temporal:
        issues.add("date_policy_marker")
    if not getattr(dataset, "PatientName", "") or not getattr(dataset, "PatientID", ""):
        issues.add("patient_pseudonym")
    if "DeidentificationMethod" not in dataset:
        issues.add("method_marker")
    return issues

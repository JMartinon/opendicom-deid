"""Batch-local entity UID mapping; never rewrite technical identifiers blindly."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Literal

from dicomanonymizer.dicomfields_selector import dicom_anonymization_database_selector
from pydicom.dataelem import DataElement
from pydicom.tag import Tag
from pydicom.uid import UID, generate_uid

TECHNICAL_UID_KEYWORDS = frozenset({
    "SOPClassUID", "ReferencedSOPClassUID", "MediaStorageSOPClassUID",
    "AffectedSOPClassUID", "RequestedSOPClassUID", "RelatedGeneralSOPClassUID",
    "OriginalSpecializedSOPClassUID", "SOPClassesInStudy", "SOPClassesSupported",
    "PertinentSOPClassesInStudy", "PertinentSOPClassesInSeries",
    "TransferSyntaxUID", "StoredInstanceTransferSyntaxUID", "AvailableTransferSyntaxUID",
    "FlowTransferSyntaxUID", "CodingSchemeUID", "ContextUID", "MappingResourceUID",
    "ContextGroupExtensionCreatorUID",
})

_ENTITY_TAGS = frozenset(
    Tag(t) for t in dicom_anonymization_database_selector("dicomfields_2024b")["U_TAGS"]
    if len(t) == 2
)
_ENTITY_KEYWORDS = frozenset({
    "AffectedSOPInstanceUID", "MediaStorageSOPInstanceUID", "SOPInstanceUID",
    "MultiFrameSourceSOPInstanceUID", "SOPInstanceUIDOfConcatenationSource",
    "SourceFrameOfReferenceUID", "TargetFrameOfReferenceUID", "VolumeFrameOfReferenceUID",
    "TableFrameOfReferenceUID", "EquipmentFrameOfReferenceUID",
    "ReferencedColorPaletteInstanceUID", "FindingsGroupUIDTrial",
    "ReferencedFindingsGroupUIDTrial", "ReferencedObservationUIDTrial",
    "AssertionUID", "ReferencedAssertionUID", "ModelGroupUID", "AnnotationGroupUID",
    "ReferencedFiducialUID", "VolumetricPresentationInputSetUID", "ReferencedContentItem",
    "TableTopPositionAlignmentUID", "SegmentationTemplateUID",
    "RadiopharmaceuticalAdministrationEventUID",
})
_REMOVE_KEYWORDS = frozenset({
    "ImplementationClassUID", "PrivateInformationCreatorUID", "CreatorVersionUID",
    "RetrieveLocationUID", "RepositoryUniqueID", "HomeCommunityID",
    "DigitalSignatureUID", "MACCalculationTransferSyntaxUID", "EncryptedContentTransferSyntaxUID",
})


def uid_kind(element: DataElement) -> Literal["entity", "technical", "remove", "unknown"]:
    """Classify by attribute semantics, never by UID string prefix.

    Unknown UI attributes fail closed. The technical allowlist is deliberately explicit;
    in particular a UID-valued Selector cannot be interpreted without its context.
    """
    if element.tag.is_private or element.tag.group in {0x0000, 0x0004, 0x0400}:
        return "remove"
    if element.keyword in _REMOVE_KEYWORDS:
        return "remove"
    if element.keyword in TECHNICAL_UID_KEYWORDS:
        return "technical"
    if element.tag in _ENTITY_TAGS or element.keyword in _ENTITY_KEYWORDS:
        return "entity"
    return "unknown"


class UIDMapper(Mapping[str, str]):
    """One ephemeral map per run, pre-populated and sealed before writing any file."""

    def __init__(self, originals: Iterable[str]) -> None:
        self._mapping: dict[str, str] = {}
        original_set = set(originals)
        for original in sorted(original_set):
            if not original or not UID(original).is_valid:
                raise ValueError("An entity UID is missing or malformed.")
            replacement = generate_uid(prefix=None)
            while replacement in original_set or replacement in self._mapping.values():
                replacement = generate_uid(prefix=None)
            self._mapping[original] = replacement

    def __getitem__(self, key: str) -> str:
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._mapping)

    def __len__(self) -> int:
        return len(self._mapping)

    def __repr__(self) -> str:
        return f"UIDMapper({len(self)} ephemeral entries)"

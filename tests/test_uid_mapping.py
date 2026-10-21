from pydicom.dataelem import DataElement
from pydicom.uid import UID, generate_uid

from opendicom_deid.core.uid_mapper import UIDMapper, uid_kind


def test_mapping_is_consistent_distinct_and_batch_local():
    originals = [generate_uid() for _ in range(100)]
    mapping = UIDMapper(originals + originals)
    assert len(mapping) == len(originals)
    assert len(set(mapping.values())) == len(originals)
    assert all(UID(value).is_valid and value.startswith("2.25.") for value in mapping.values())
    assert not set(mapping.values()).intersection(originals)
    assert mapping[originals[0]] == mapping[originals[0]]
    assert mapping[originals[0]] != UIDMapper(originals)[originals[0]]
    assert originals[0] not in repr(mapping)


def test_uid_classification_preserves_technical_meaning():
    assert uid_kind(DataElement(0x00080016, "UI", "1.2.3")) == "technical"
    assert uid_kind(DataElement(0x00020010, "UI", "1.2.3")) == "technical"
    assert uid_kind(DataElement(0x00081150, "UI", "1.2.3")) == "technical"
    assert uid_kind(DataElement(0x00080018, "UI", "1.2.3")) == "entity"
    assert uid_kind(DataElement(0x00081155, "UI", "1.2.3")) == "entity"
    assert uid_kind(DataElement(0x00200052, "UI", "1.2.3")) == "entity"

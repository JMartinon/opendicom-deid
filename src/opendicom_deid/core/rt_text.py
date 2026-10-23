"""Clinical RT labels and descriptions, with known-identifier span removal.

These fields have clinical meaning but may also contain free text. Screening is
limited to identifiers known in the input batch; local privacy review is needed.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

from .equipment_text import IdentifierToken

RT_TEXT_KEYWORDS = frozenset({
    "RTImageLabel", "RTImageName", "RTImageDescription", "RadiationMachineName", "GantryID",
    "DoseComment", "StructureSetLabel", "StructureSetName", "StructureSetDescription",
    "ROIName", "ROIDescription", "ROIGenerationDescription", "ROIObservationLabel",
    "ROIObservationDescription", "FrameOfReferenceTransformationComment",
    "MeasuredDoseDescription", "CalculatedDoseReferenceDescription",
    "RTPlanLabel", "RTPlanName", "RTPlanDescription", "TreatmentProtocols", "TreatmentSites",
    "PrescriptionDescription", "DoseReferenceDescription", "ToleranceTableLabel",
    "FractionGroupDescription", "FractionPattern", "BeamName", "BeamDescription",
    "ImagingDeviceSpecificAcquisitionParameters", "WedgeID", "BolusID", "BolusDescription",
    "CompensatorID", "CompensatorTrayID", "CompensatorDescription", "BlockTrayID",
    "AccessoryCode", "BlockName", "ApplicatorID", "ApplicatorDescription", "PatientSetupLabel",
    "PatientAdditionalPosition", "FixationDeviceLabel", "FixationDeviceDescription",
    "FixationDevicePosition", "ShieldingDeviceLabel", "ShieldingDeviceDescription",
    "ShieldingDevicePosition", "SetupTechniqueDescription", "SetupDeviceLabel",
    "SetupDeviceDescription", "SetupReferenceDescription", "SourceManufacturer",
    "SourceModelID", "SourceDescription", "SourceIsotopeName", "ApplicationSetupName",
    "ApplicationSetupManufacturer", "TemplateType", "TemplateName", "BrachyAccessoryDeviceID",
    "BrachyAccessoryDeviceName", "AfterloaderChannelID", "SourceApplicatorID",
    "SourceApplicatorName", "SourceApplicatorManufacturer", "ChannelShieldID", "ChannelShieldName",
    "SnoutID", "RangeShifterID", "RangeShifterDescription", "LateralSpreadingDeviceID",
    "LateralSpreadingDeviceDescription", "RangeModulatorID", "RangeModulatorDescription",
    "BeamCurrentModulationID", "PatientSupportID", "PatientSupportAccessoryCode",
    "TrayAccessoryCode", "RangeShifterSetting", "LateralSpreadingDeviceSetting",
    "ScanSpotTuneID", "GeneralAccessoryID", "GeneralAccessoryDescription",
    "RTPrescriptionLabel", "RTTreatmentApproachLabel", "RTPhysicianIntentNarrative",
    "PriorTreatmentDoseDescription", "TreatmentSite", "TreatmentTechniqueNotes",
    "PrescriptionNotes", "FractionationNotes", "EffectiveDoseCalculationMethodDescription",
})
RT_GROUPS = frozenset({0x3002, 0x3004, 0x3006, 0x3008, 0x300A, 0x300C, 0x3010})


def clean_rt_value(value: str, identifiers: Iterable[IdentifierToken]) -> str:
    """Remove matched original spans without discarding unrelated clinical words."""
    normalized: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(value):
        for part in unicodedata.normalize("NFKD", char).casefold():
            if part.isalnum():
                normalized.append(part)
                offsets.append(index)
    search = "".join(normalized)
    spans: list[tuple[int, int]] = []
    for token, short_exact_only in identifiers:
        if not token:
            continue
        start = 0
        while (position := search.find(token, start)) >= 0:
            left, right = offsets[position], offsets[position + len(token) - 1] + 1
            if len(token) >= 3 or search == token or (
                not short_exact_only
                and (left == 0 or not value[left - 1].isalnum())
                and (right == len(value) or not value[right].isalnum())
            ):
                # Include decomposed accent marks attached to the last letter.
                while right < len(value) and unicodedata.combining(value[right]):
                    right += 1
                spans.append((left, right))
            start = position + 1
    if not spans:
        return value
    spans.sort()
    result: list[str] = []
    end = 0
    for left, right in spans:
        if left > end:
            result.append(value[end:left])
        end = max(end, right)
    result.append(value[end:])
    cleaned = "".join(result).strip()
    return cleaned if any(char.isalnum() for char in cleaned) else "ANONYMIZED"


def clean_rt_values(values: tuple[str, ...], identifiers: Iterable[IdentifierToken]) -> tuple[str, ...]:
    return tuple(clean_rt_value(value, identifiers) for value in values)

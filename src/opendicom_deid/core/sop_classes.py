"""Supported storage classes and safe modality labels shared by both scan passes."""

from __future__ import annotations

import re

from pydicom.dataset import Dataset
from pydicom.uid import UID, UID_dictionary

# Exact class reported by a ViewRay export. Support is limited to its public
# RT beam/control-point representation; this is not an assertion of equivalence
# to standard RT Plan Storage. Preserve this UID, including class references.
VIEWRAY_RT_PLAN_STORAGE = "2.16.840.1.114493.1.2.1.4.1.1.481.5"
KNOWN_PRIVATE_STORAGE_CLASSES = {VIEWRAY_RT_PLAN_STORAGE: "ViewRay RT Plan (private storage)"}
VIEWRAY_PLAN_WARNING = (
    "ViewRay private RT plan: selected public beam/control-point values and references are checked; "
    "vendor-private fields are removed. Proprietary treatment content may be lost. "
    "Completeness for independent dose reconstruction or treatment-system re-import is not verified."
)
VIEWRAY_PLAN_REVIEW_REQUIRED = (
    "This is a ViewRay private RT plan. Full proprietary plan preservation is unsupported. "
    "For public DICOM data only, enable 'ViewRay public-data research export' in Advanced options "
    "(--viewray-public-plan in the CLI). This removes vendor-private treatment fields and is "
    "not validated for independent dose reconstruction."
)

# SC Modality describes the source of a capture and is optional (Type 3),
# overriding General Series. PS3.3 C.8.6.1, Table C.8-24, Note 2:
# https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.6.html
SECONDARY_CAPTURE_CLASSES = frozenset({
    "1.2.840.10008.5.1.4.1.1.7",
    "1.2.840.10008.5.1.4.1.1.7.1",
    "1.2.840.10008.5.1.4.1.1.7.2",
    "1.2.840.10008.5.1.4.1.1.7.3",
    "1.2.840.10008.5.1.4.1.1.7.4",
})

# Deliberate scope: images, conventional RT, and one gated public-data RT export.
# Recognition here does not bypass the explicit ViewRay research-mode gate.
# None means SC source modality semantics, not an unchecked storage class.
SUPPORTED_SOP_CLASSES: dict[str, frozenset[str] | None] = {
    "1.2.840.10008.5.1.4.1.1.1": frozenset({"CR"}),
    "1.2.840.10008.5.1.4.1.1.1.1": frozenset({"DX", "IO", "MG", "PX"}),
    "1.2.840.10008.5.1.4.1.1.1.1.1": frozenset({"DX", "IO", "MG", "PX"}),
    "1.2.840.10008.5.1.4.1.1.1.2": frozenset({"MG"}),
    "1.2.840.10008.5.1.4.1.1.1.2.1": frozenset({"MG"}),
    "1.2.840.10008.5.1.4.1.1.2": frozenset({"CT"}),
    "1.2.840.10008.5.1.4.1.1.2.1": frozenset({"CT"}),
    "1.2.840.10008.5.1.4.1.1.2.2": frozenset({"CT"}),
    "1.2.840.10008.5.1.4.1.1.3.1": frozenset({"US"}),
    "1.2.840.10008.5.1.4.1.1.4": frozenset({"MR"}),
    "1.2.840.10008.5.1.4.1.1.4.1": frozenset({"MR"}),
    "1.2.840.10008.5.1.4.1.1.4.2": frozenset({"MR"}),
    "1.2.840.10008.5.1.4.1.1.4.3": frozenset({"MR"}),
    "1.2.840.10008.5.1.4.1.1.4.4": frozenset({"MR"}),
    "1.2.840.10008.5.1.4.1.1.6.1": frozenset({"US"}),
    "1.2.840.10008.5.1.4.1.1.6.2": frozenset({"US"}),
    **dict.fromkeys(SECONDARY_CAPTURE_CLASSES),
    "1.2.840.10008.5.1.4.1.1.12.1": frozenset({"XA", "RF"}),
    "1.2.840.10008.5.1.4.1.1.12.2": frozenset({"RF"}),
    "1.2.840.10008.5.1.4.1.1.20": frozenset({"NM"}),
    "1.2.840.10008.5.1.4.1.1.128": frozenset({"PT"}),
    "1.2.840.10008.5.1.4.1.1.130": frozenset({"PT"}),
    "1.2.840.10008.5.1.4.1.1.481.1": frozenset({"RTIMAGE"}),
    "1.2.840.10008.5.1.4.1.1.481.2": frozenset({"RTDOSE"}),
    "1.2.840.10008.5.1.4.1.1.481.3": frozenset({"RTSTRUCT"}),
    "1.2.840.10008.5.1.4.1.1.481.5": frozenset({"RTPLAN"}),
    "1.2.840.10008.5.1.4.1.1.481.8": frozenset({"RTPLAN"}),
    VIEWRAY_RT_PLAN_STORAGE: frozenset({"RTPLAN"}),
}

# Known defined terms (including retired terms), PS3.3 C.7.3.1.1.1. This is a
# report-label allowlist, not a claim that other valid CS extensions are illegal.
KNOWN_MODALITIES = frozenset(
    "ANN AR ASMT AU BDUS BI BMD CFM CR CT CTPROTOCOL DMS DG DOC DX ECG EEG EMG "
    "EOG EPS ES FID GM HC HD IO IOL IVOCT IVUS KER KO LEN LS MG MR M3D NM OAM "
    "OCT OP OPM OPT OPTBSV OPTENF OPV OSS OT PA PLAN POS PR PT PX REG RESP RF RG "
    "RTDOSE RTIMAGE RTINTENT RTPLAN RTRAD RTRECORD RTSEGANN RTSTRUCT RWV SEG SM "
    "SMR SR SRF STAIN TEXTUREMAP TG US VA XA XAPROTOCOL XC AS CD CF CP CS DD DF "
    "DM DS EC FA FS LP MA MS OPR ST VF".split()
)


def modality_label(ds: Dataset) -> str:
    """Never expose arbitrary source strings as report labels."""
    value = str(ds.get("Modality") or "").strip()
    if not value:
        return "Unspecified"
    return value if value in KNOWN_MODALITIES else "Other modality"


def storage_class_name(sop_class: str) -> str:
    return KNOWN_PRIVATE_STORAGE_CLASSES.get(sop_class) or UID(sop_class).name


def unsupported_object_message(ds: Dataset) -> str:
    """Identify standard storage classes without echoing arbitrary source UIDs.

    UID.name falls back to the input value for unknown UIDs. Look up the trusted
    dictionary first so private class identifiers cannot leak into public reports.
    """
    sop_class = str(ds.get("SOPClassUID", ""))
    definition = UID_dictionary.get(sop_class)
    modality = modality_label(ds)
    if definition is not None and definition[1] == "SOP Class":
        return (
            f"Unsupported DICOM SOP class: {definition[0]} ({sop_class}); "
            f"modality: {modality}. This storage class requires dedicated "
            "de-identification support."
        )
    return (
        f"An unrecognized or private DICOM SOP class was found; modality: {modality}. "
        "Check SOP Class UID (0008,0016) locally to identify the export format; "
        "dedicated de-identification support is required."
    )


def modality_error(ds: Dataset) -> str | None:
    """Check supported modality semantics without altering source metadata."""
    sop_class = str(ds.get("SOPClassUID", ""))
    if sop_class not in SUPPORTED_SOP_CLASSES:
        return "An unsupported DICOM storage class requires specialized handling."
    value = ds.get("Modality")
    modality = str(value or "").strip()
    if not modality:
        if sop_class in SECONDARY_CAPTURE_CLASSES:
            return None
        return f"{storage_class_name(sop_class)} requires a Modality (0008,0060) value."
    if not isinstance(value, str) or re.fullmatch(r"[A-Z0-9_ ]{1,16}", modality) is None:
        return "Modality (0008,0060) must contain one valid DICOM code string."
    expected = SUPPORTED_SOP_CLASSES[sop_class]
    if expected is not None and modality not in expected:
        actual = modality if modality in KNOWN_MODALITIES else "an unrecognized value"
        return (
            f"{storage_class_name(sop_class)} expects Modality {', '.join(sorted(expected))}; "
            f"found {actual}."
        )
    return None

"""Value-free, read-only metadata screening; never a privacy certification.

Only populated fields and review boundaries are reported. A field may contain a
real identifier, a pseudonym, or a dummy value; this screen cannot decide which.
Private content remains unresolved even when known identifiers are not found.
Pixels, compressed/encrypted content, unknown encodings and metadata after pixels
are not screened. Matching is additive and deliberately incomplete.

Files and individual loaded values are size-limited, and traversal has explicit
depth/item/element limits. pydicom may eagerly parse undefined-length sequences
before traversal; those limits are not hard bounds on parser memory or depth.
Parser failures, including recursion errors, are reported without source text.
"""

from __future__ import annotations

import re
from pathlib import Path
from threading import Event

import pydicom
from pydicom.datadict import dictionary_VR, keyword_for_tag
from pydicom.dataelem import DataElement, RawDataElement
from pydicom.dataset import Dataset
from pydicom.errors import InvalidDicomError
from pydicom.filereader import read_file_meta_info
from pydicom.uid import DeflatedExplicitVRLittleEndian

from .diagnostics import private_diagnostics
from .models import Cancelled, Finding, check_cancel
from .scanner import _paths
from .uid_mapper import uid_kind

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_VALUE_BYTES = 1024 * 1024
MAX_DEPTH = 16
MAX_ITEMS = 10_000
MAX_ELEMENTS = 20_000
MAX_FINDINGS = 500
MAX_MATCH_TOKENS = 64

_DIRECT_KEYWORDS = frozenset({
    "PatientName", "PatientID", "OtherPatientIDs", "OtherPatientNames",
    "PatientBirthName", "PatientMotherBirthName", "PatientAddress",
    "PatientTelephoneNumbers", "PatientTelecomInformation", "MedicalRecordLocator",
    "AccessionNumber", "AdmissionID", "RequestedProcedureID", "StudyID",
})
_TEXT_VRS = frozenset({"AE", "LO", "LT", "SH", "ST", "UC", "UT", "UR"})
_OPAQUE_VRS = frozenset({"OB", "OD", "OF", "OL", "OV", "OW", "UN", "OB or OW"})
_SUMMARY_KEYS = (
    "direct_identifiers", "person_names", "dates", "free_text", "entity_uids",
    "private_elements", "opaque_elements", "private_identifier_matches", "unreadable_elements",
)
_LIMITATIONS = [
    "Populated fields are potential identifying information; this does not establish whether values are real or synthetic.",
    "A lack of findings does not establish anonymity. Privacy review is always required.",
    "Private, compressed, encrypted, and unknown content remains unresolved. Absence of a known-identifier match is not proof of absence.",
    "Pixels and metadata after the first pixel-data element are not screened.",
    "Original entity and reference UIDs can remain linkable and require consistent remapping before sharing.",
    "Traversal is bounded, but pydicom can parse undefined-length sequences before traversal limits are applied.",
]


def _failure(status: str, message: str) -> dict:
    return {
        "inspection_type": "privacy", "status": status,
        "privacy_review": "REQUIRED", "message": message,
    }


class _Screen:
    def __init__(self, file_id: int, cancel: Event | None) -> None:
        self.cancel = cancel
        self.result = {
            "inspection_type": "privacy", "status": "REVIEW_REQUIRED",
            "file_id": f"File {file_id:06d}", "privacy_review": "REQUIRED",
            "coverage": "LIMITED", "limits_reached": False, "findings_truncated": False,
            "summary": dict.fromkeys(_SUMMARY_KEYS, 0), "findings": [],
            "limitations": list(_LIMITATIONS),
        }
        self.elements = 0
        self.items = 0
        self.tokens: set[bytes] = set()
        self.private_values: list[tuple[str, bytes]] = []
        self.stop = False

    def add(self, code: str, path: str, keyword: str, message: str, count: str = "") -> None:
        if count:
            self.result["summary"][count] += 1
        if len(self.result["findings"]) >= MAX_FINDINGS:
            self.result["findings_truncated"] = True
            return
        self.result["findings"].append({
            "code": code, "tag_path": path, "keyword": keyword, "message": message,
        })

    def limited(self, path: str, keyword: str = "") -> None:
        self.result["limits_reached"] = True
        self.add("INSPECTION_LIMIT", path, keyword, "A screening limit prevented further inspection of this content.")

    def unreadable(self, path: str, keyword: str = "") -> None:
        self.add("ELEMENT_UNREADABLE", path, keyword, "Content could not be safely inspected and remains unresolved.", "unreadable_elements")

    def remember_tokens(self, raw: bytes, keyword: str) -> None:
        # Minimum length reduces incidental short/numeric matches. Only small,
        # meaningful source identifiers are retained, solely in process memory.
        candidates = [raw.strip(b" \x00")]
        if keyword == "PatientName":
            candidates.extend(re.split(rb"[\^=\\]", raw))
        for candidate in candidates:
            candidate = candidate.strip(b" \x00")
            if not 4 <= len(candidate) <= 256:
                continue
            forms = {candidate}
            try:
                text = candidate.decode("utf-8")
                forms.update((text.encode("utf-16-le"), text.encode("utf-16-be")))
            except UnicodeError:
                pass
            for form in forms:
                if len(self.tokens) >= MAX_MATCH_TOKENS:
                    self.limited("Header")
                    return
                self.tokens.add(form)

    def walk(self, dataset: Dataset, prefix: str = "", depth: int = 0) -> None:
        check_cancel(self.cancel)
        if depth > MAX_DEPTH:
            self.limited(prefix)
            return
        for tag in dataset.keys():
            check_cancel(self.cancel)
            if self.stop:
                return
            if self.elements >= MAX_ELEMENTS:
                self.limited(prefix or "Header")
                self.stop = True
                return
            self.elements += 1
            path = f"{prefix}/" if prefix else ""
            path += f"({tag.group:04X},{tag.element:04X})"
            keyword = "" if tag.is_private else keyword_for_tag(tag)
            if tag.is_private:
                self.add("PRIVATE_CONTENT_UNRESOLVED", path, "", "Private content requires vendor-aware local review; it is not cleared by this screen.", "private_elements")
            try:
                element = dataset.get_item(tag, keep_deferred=True)
                if element is None:
                    self.unreadable(path, keyword)
                    continue
                vr = element.VR
                if vr is None and not tag.is_private:
                    try:
                        vr = dictionary_VR(tag)
                    except KeyError:
                        vr = "UN"
                value = element.value
                if isinstance(element, RawDataElement) and value is None and element.length:
                    self.limited(path, keyword)
                    self.add("OPAQUE_CONTENT_UNRESOLVED", path, keyword, "Deferred content was not loaded or screened.", "opaque_elements")
                    continue
                if vr == "SQ":
                    if isinstance(element, RawDataElement):
                        # Defined-length sequences are converted only after the
                        # deferred-value check, never for an oversized value.
                        value = dataset[tag].value
                    for index, item in enumerate(value or []):
                        check_cancel(self.cancel)
                        if self.items >= MAX_ITEMS:
                            self.limited(path, keyword)
                            self.stop = True
                            return
                        self.items += 1
                        if isinstance(item, Dataset):
                            self.walk(item, f"{path}[{index}]", depth + 1)
                        else:
                            self.unreadable(path, keyword)
                        if self.stop:
                            return
                    continue
                if isinstance(value, bytes):
                    raw = value
                elif isinstance(value, str):
                    raw = value.encode("utf-8", errors="replace")
                elif vr in _TEXT_VRS | {"PN", "DA", "DT", "TM", "UI"}:
                    raw = str(value).encode("utf-8", errors="replace") if value is not None else b""
                else:
                    raw = b""
                if len(raw) > MAX_VALUE_BYTES:
                    self.limited(path, keyword)
                    continue
                populated = bool(raw.strip(b" \x00"))
                if tag.is_private:
                    if raw:
                        self.private_values.append((path, raw))
                    self.add("OPAQUE_CONTENT_UNRESOLVED", path, "", "The meaning and completeness of private content are not established.", "opaque_elements")
                elif vr in _OPAQUE_VRS or not keyword:
                    self.add("OPAQUE_CONTENT_UNRESOLVED", path, keyword, "Opaque or unknown content requires additional local review.", "opaque_elements")
                if not populated:
                    continue
                if keyword in _DIRECT_KEYWORDS:
                    self.add("DIRECT_IDENTIFIER_PRESENT", path, keyword, "A field commonly used for an identifying value is populated.", "direct_identifiers")
                if vr == "PN":
                    self.add("PERSON_NAME_PRESENT", path, keyword, "A person-name field is populated and requires review.", "person_names")
                elif vr in {"DA", "DT", "TM"}:
                    self.add("DATE_PRESENT", path, keyword, "A date or time field is populated and requires review.", "dates")
                elif vr in _TEXT_VRS and not tag.is_private:
                    self.add("FREE_TEXT_PRESENT", path, keyword, "A text field is populated; clinical text can also contain identifying information.", "free_text")
                if vr == "UI" and not tag.is_private:
                    kind = uid_kind(DataElement(tag, "UI", ""))
                    if kind == "entity":
                        self.add("ENTITY_UID_PRESENT", path, keyword, "An original entity or reference UID is present and requires consistent remapping before sharing.", "entity_uids")
                    elif kind != "technical":
                        self.add("OPAQUE_CONTENT_UNRESOLVED", path, keyword, "A nontechnical UID requires removal or additional local review.", "opaque_elements")
                if keyword in {"PatientName", "PatientID"}:
                    self.remember_tokens(raw, keyword)
            except Cancelled:
                raise
            except Exception:
                self.unreadable(path, keyword)

    def match_private(self) -> None:
        for path, raw in self.private_values:
            check_cancel(self.cancel)
            if any(token in raw for token in self.tokens):
                self.add("PRIVATE_IDENTIFIER_MATCH", path, "", "Private content contains a possible match to a known public identifier; review it locally.", "private_identifier_matches")


def inspect_privacy(
    directory: Path,
    file_id: int,
    recursive: bool = True,
    cancel: Event | None = None,
) -> dict:
    """Screen one numbered source file without modifying it or returning values.

    File numbers follow the same unchanged input inventory as the application.
    Private elements are retained in memory for screening, never stripped or
    numerically converted. No result constitutes a finding of anonymity.
    """
    try:
        check_cancel(cancel)
        if type(file_id) is not int or file_id < 1:
            return _failure("FAILED", "File numbers must be positive.")
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            return _failure("FAILED", "Select a readable input directory.")
        findings: list[Finding] = []
        paths = _paths(root, recursive, findings, cancel)
        check_cancel(cancel)
        if findings:
            return _failure("FAILED", "The input inventory contains excluded or unreadable entries.")
        if file_id > len(paths):
            return _failure("FAILED", "The requested File number is outside this inventory.")
        source = paths[file_id - 1]
        if source.stat().st_size > MAX_FILE_BYTES:
            return _failure("FAILED", "The selected file exceeds the metadata screening size limit.")
        screen = _Screen(file_id, cancel)
        with private_diagnostics() as diagnostics:
            try:
                # Deflated datasets can expand far beyond their on-disk size.
                # Inspect file meta first and leave that encoding unresolved.
                try:
                    meta = read_file_meta_info(source)
                except InvalidDicomError:
                    meta = Dataset()
                if meta.get("TransferSyntaxUID") == DeflatedExplicitVRLittleEndian:
                    return _failure("FAILED", "Deflated datasets require specialized local privacy review.")
                check_cancel(cancel)
                try:
                    dataset = pydicom.dcmread(source, defer_size=MAX_VALUE_BYTES, stop_before_pixels=True)
                except InvalidDicomError:
                    dataset = pydicom.dcmread(source, force=True, defer_size=MAX_VALUE_BYTES, stop_before_pixels=True)
                check_cancel(cancel)
                if 0x00080016 not in dataset:
                    return _failure("FAILED", "The selected DICOM header could not be safely inspected.")
                preamble = getattr(dataset, "preamble", None)
                if preamble and any(preamble):
                    screen.add("OPAQUE_CONTENT_UNRESOLVED", "FilePreamble", "", "A nonempty file preamble requires additional local review.", "opaque_elements")
                screen.walk(getattr(dataset, "file_meta", Dataset()))
                screen.walk(dataset)
                screen.match_private()
            except Cancelled:
                raise
            except Exception:
                screen.unreadable("Header")
        if diagnostics.has_warnings:
            screen.unreadable("Header")
        check_cancel(cancel)
        return screen.result
    except Cancelled:
        return _failure("CANCELLED", "Privacy inspection was cancelled.")
    except Exception:
        return _failure("FAILED", "The selected input could not be safely inspected.")

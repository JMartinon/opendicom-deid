# Metadata policy and privacy review

OpenDICOM De-ID implements a documented metadata transformation for research and
teaching exports. It does **not** claim DICOM confidentiality-profile conformance,
regulatory compliance, suitability for treatment, or removal of all identifying
information. A technically valid export still requires a privacy review.

## Basis and scope

The baseline is the Basic Application Level Confidentiality Profile attribute
table provided by Kitware's `dicom-anonymizer` 2.1.0 for DICOM 2024b. The application
imports the rule tables without modifying the installed dependency. Its own code
controls recursion, UID replacement, and metadata removal.

[DICOM PS3.15 Annex E](https://dicom.nema.org/medical/dicom/current/output/chtml/part15/chapter_E.html)
was reviewed as version **2026d** during development on 2026-10-21. It requires
consistent protection of references at every nesting level, distinguishes
technical identifiers from entity identifiers, and requires replacement of file
meta and preamble. Table actions must also respect the attribute's IOD context.
Using an older rule baseline with additional conservative safeguards is not a
verified implementation of the current profile. No CID 7050 profile-conformance
code is written.

## Implemented behavior

| Metadata | Behavior |
| --- | --- |
| Patient name and patient ID | Replaced by a batch-consistent pseudonym, including occurrences inside retained sequences. |
| Entity UIDs | A batch mapping is applied at all nesting depths, including external references. Unsupported UID meanings fail processing. |
| Technical UIDs | Explicitly recognized class, syntax, and other technical identifiers are preserved. |
| Private attributes | Removed, including private creators, before value conversion whenever possible. There is no private-tag retention option. |
| Ordinary text | Nonempty text becomes `ANONYMIZED`, or is removed under the baseline rules. ROI, beam, study and protocol labels may lose meaningful names. Numeric references remain intact. |
| Coded meanings and units | Code values, coding scheme identifiers/versions, code meanings and rescale units are retained when their containing sequence is retained. These require review for local codes or identifying content. |
| Dates and times | Cleared by default. Optional date shifting uses a nonzero offset consistent for a patient within the batch. |
| History and signatures | Original/modified/encrypted attributes, digital signatures and related provenance containers are removed. |
| Graphics and icons | Standalone overlay/curve groups, graphic annotations and icon images are removed. Embedded pixel annotations are not detected or removed. |
| Clinical arrays | Pixel bytes, geometry and retained RT numerical arrays are not intentionally changed. Validation compares selected attributes. |
| File meta | Rebuilt by the engine with the output instance/class and preserved transfer syntax. The preamble is replaced. |

Sequence items containing live entity references are retained and individually
scrubbed, even when a broad baseline sequence action would otherwise empty or
remove them. Historical/signature/icon containers are an explicit exception and
are always discarded. These overrides preserve arbitrary nested RT references;
they are another reason not to assert blanket profile conformance.

Public unknown-value (`UN`) attributes and protected opaque binary fields fail
closed. The engine also limits supported SOP classes. New standard attributes,
locally extended objects and unexpected vendor encodings require investigation.

## Dates and quantitative uses

Default removal sets DA, DT and TM values to zero length, including attributes not
listed in the baseline. Some IODs require nonempty dates/times. Consequently an
export can pass the application's readability and relationship checks while
still requiring repair or rejection by an independent IOD conformance validator.

Birth dates and birth times are always cleared, including in shift mode.
Shifting other dates preserves full calendar dates, available time precision, fractional
seconds, clock times, and timezone information; it shifts DA and DT by the same
whole-day offset. Empty values remain empty. Partial or malformed dates, invalid
times and overflows fail processing instead of being interpreted heuristically.
The offset and identity mapping are held only in memory and are not included in
reports. Separate batches receive independent transformations.

Time retention may preserve recognizable appointment patterns and timezone hints.
The method is inspired by, but does not claim conformance to, the
[Modified Dates option](https://dicom.nema.org/medical/dicom/current/output/chtml/part15/sect_E.3.6.html).
Date removal can affect time-dependent quantitative analysis; neither policy is a
substitute for reviewing the intended research workflow.

## Why the Kitware mutation path is not used

Inspection of [Kitware's implementation](https://github.com/KitwareMedical/dicom-anonymizer/blob/master/dicomanonymizer/simpledicomanonymizer.py)
found that ordinary tag actions are applied to the supplied dataset; the special
repeating-group path walks items recursively. Other sequence actions blanket
replace or empty their children. The upstream UID map is module-global. These
behaviors do not establish safe batch-wide handling of nested RT references.
OpenDICOM uses a separate batch map and recursive policy application; it neither
patches installed source nor changes Kitware's global map.

## Malformed vendor metadata

The ViewRay example `(01F1,1026)` can contain six bytes such as `b'0.938 '` while
declaring an eight-byte floating-point VR. Iterating converted values before
deleting private tags can raise `BytesLengthException`. The reader removes private
elements by tag before asking pydicom to decode their contents, recursively within
public sequences. The application does not globally enable
`convert_wrong_length_to_UN`: such a process-wide switch could also disguise
malformed public clinical data. If a file cannot be parsed safely, it is failed.

## What PASS means

Readability, supported metadata checks, reference integrity and selected
preservation comparisons are separate technical results. External references
already absent in the input remain explicit findings. Successful technical checks
do not prove that the IOD is complete or that a person cannot be identified.

Before sharing, review pixels for burned-in identifiers, recognizable anatomy,
unusual local codes and coded descriptions, source-specific metadata, and any
warnings. Images can be linked to originals by their unchanged pixels. Private
attribute removal may also remove vendor information needed by a downstream
application. Use only copies for research or teaching, and independently verify
the intended downstream application with representative synthetic and authorized
test data before trusting an export.

No real patient datasets belong in source control, test fixtures, screenshots or
issue attachments. Reports and logs must omit original identities, source paths,
original UIDs and UID mappings. The application has no upload or telemetry path.

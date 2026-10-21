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
| Selected equipment and material text | `Manufacturer`, `ManufacturerModelName`, `SoftwareVersions`, `TreatmentMachineName`, `FluenceModeID` and `MaterialID` are retained unless they match known identifiers from the batch. Matches become `ANONYMIZED` and produce a value-free warning. |
| Beam names | User-defined names are replaced by neutral labels such as `BEAM-1`, based on the unchanged beam number. Original beam labels are not retained. |
| Other ordinary text | Nonempty text becomes `ANONYMIZED`, or is removed under the baseline rules. ROI, study and protocol labels may lose meaningful names. Serial numbers, institution details, descriptions and plan labels remain scrubbed. Numeric references remain intact. |
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

One observed ViewRay private RTPLAN class has an opt-in public-data research
export mode, documented in [the validation evidence](validation.md#viewray-private-rt-plan-research-export).
It does not retain vendor-private treatment fields or claim preservation of a
complete proprietary plan. Public beam/control-point content is required and
checked, while the original class UID is preserved. A fixed warning is emitted
for each such plan. The default remains to reject it until the research export
option is explicitly enabled. This option does not relax metadata scrubbing or
allow other private storage classes.

This mode is not validated for independent dose reconstruction. The six selected
public text fields use the screening policy below, but other public text remains
subject to scrubbing. Retaining selected text, numeric parameters and pixels does
not establish preservation of all inputs needed by an external dose calculator.
A vendor-specific private retention policy would need documented field meanings,
identification risks, and reference handling. No such policy is implemented for
this class. See the DICOM
[Retain Safe Private Option](https://dicom.nema.org/medical/dicom/current/output/chtml/part15/sect_E.3.10.html)
for the distinction between known safe private attributes and arbitrary private
data.

This distinction matters because [DICOM PS3.2 Section 7.3](https://dicom.nema.org/Medical/dicom/current/output/chtml/part02/sect_7.3.html)
allows specialized SOP classes to require private attributes. A class UID alone
does not establish that deleting them leaves a complete treatment object.

## Machine and beam text

Version 0.1.6 replaces blanket scrubbing of six public technical text fields with
an explicit allowlist: `Manufacturer`, `ManufacturerModelName`, `SoftwareVersions`,
`TreatmentMachineName`, `FluenceModeID`, and `MaterialID`. These fields can identify
equipment or select a model needed to interpret public treatment data. The rule
applies at every retained sequence depth, with the same behavior for standard
and explicitly supported private storage classes.

Before transforming files, the engine gathers known patient names, identifiers
and entity/reference UIDs across the batch, including clinical-trial subject and
subject-reading identifiers. It screens allowlisted text against these values
using normalization for case, punctuation and diacritics. Longer tokens are
matched within text. Short identifiers match complete delimited components, so
a patient ID of `42` matches `LINAC-42`. A short numeric `StudyID` must match the
whole field instead, to avoid incidental matches in software version numbers.
When a match is found, the affected value becomes `ANONYMIZED` and a fixed
warning is reported without source values. This bounded check cannot identify
arbitrary names, aliases, identifiers absent from the known fields, or every
encoding.
Retained text and machine labels still require review locally; device identity
can also link a dataset to a particular site.

`BeamName` is not on that allowlist. It is a user-defined name and is replaced by
`BEAM-<BeamNumber>` for a valid beam number, with a compact `B<number>` form for
long numbers to fit the field's length limit. Empty names stay empty; a name
without a usable beam number becomes `ANONYMIZED`. Distinct valid beam numbers keep
distinct labels without preserving arbitrary source names. Beam numbers and their
references, control points, meterset weights, geometry, and MLC data retain the
existing numerical and reference checks. Output verification compares the exact
expected equipment text and generated beam labels with the saved files, including
values that identifier screening required the policy to scrub.

The [DICOM RT Beams module](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.8.14.html)
distinguishes beam numbers, user-defined names, and equipment parameters. The
[Retain Device Identity Option](https://dicom.nema.org/medical/dicom/current/output/chtml/part15/sect_E.3.8.html)
recognizes both the analytical value and privacy implications of retaining device
information. This narrower application policy does not implement or claim
conformance to that option: serial numbers and other device-identifying fields
outside the allowlist remain scrubbed. It also does not establish clinical
completeness or suitability for independent dose reconstruction.

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
original entity UIDs and UID mappings. Known storage-class UIDs are technical
format identifiers and may appear in diagnostics. The application has no upload
or telemetry path.

# Metadata policy and privacy review — 0.2.0

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
| Technical UIDs | Explicitly recognized class, syntax, and other technical identifiers are preserved, except for the narrowly scoped ViewRay reference-class correction described below. |
| Private attributes | Removed, including private creators, before value conversion whenever possible. There is no private-tag retention option. |
| Selected equipment and material text | `Manufacturer`, `ManufacturerModelName`, `SoftwareVersions`, `TreatmentMachineName`, `FluenceModeID` and `MaterialID` are retained unless they match known identifiers from the batch. Matches become `ANONYMIZED` and produce a value-free warning. |
| Clinical RT labels and descriptions | The default `clinical` policy retains an explicit allowlist, including ROI/beam/plan names, prescription descriptions and notes, after removing spans matching known identifiers. The optional `strict` policy replaces or removes this text and uses neutral beam labels such as `BEAM-1`. |
| Staff, institution and device identifiers | Person-name fields, hospital details and serial numbers remain removed or scrubbed. Known values are also used to screen retained text. |
| Other ordinary text | Nonempty text outside the clinical/equipment allowlists and coded exceptions becomes `ANONYMIZED`, or is removed under the baseline rules. Study and series descriptions remain scrubbed. Numeric references remain intact. |
| Coded meanings and units | Code values, coding scheme identifiers/versions, code meanings and rescale units are retained when their containing sequence is retained. These require review for local codes or identifying content. |
| Dates and times | Cleared by default. Optional date shifting uses a nonzero offset consistent for a patient within the batch. |
| History and signatures | Original/modified/encrypted attributes, digital signatures and related provenance containers are removed. |
| Graphics and icons | Standalone overlay/curve groups, graphic annotations and icon images are removed. Embedded pixel annotations are not detected or removed. |
| Clinical arrays and sequence structure | Pixel bytes, geometry and retained RT numerical arrays are not intentionally changed. Validation compares selected numeric values and retained RT sequence item counts at their original paths. |
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
equipment fields and curated clinical RT fields follow the text policies below;
other public text remains subject to scrubbing. Retaining text, numeric parameters and pixels does
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

## ViewRay reference-class correction

Since version 0.1.7, the ViewRay research export option permits one type of
reference correction. A standard `RTPlanStorage` `ReferencedSOPClassUID` is changed to the
supported private ViewRay class only when its single `ReferencedSOPInstanceUID`
resolves unambiguously to that private RTPLAN in the selected batch and the
source and target have the same patient grouping key. The target plan's own
class UID is never rewritten. Instance UIDs and references still receive the
usual consistent anonymization mapping.

The correction changes only the eligible reference-class field at its exact
sequence location. It does not guess from the vendor name, a UID prefix, or the
modality alone. Missing targets, reverse or unrelated class pairs, conflicting
duplicates and cross-patient references are not eligible. The scanner reports
remaining class mismatches before transformation. Findings identify neutral
source/target file numbers, numeric tag paths and recognized class names;
original entity UIDs, paths and unknown class values are omitted.

Each correction produces a `VIEWRAY_REFERENCE_CLASS_REPAIR` warning. The
validator independently recomputes the expected corrections from the original
sources, then checks the saved output at those locations. Other class changes
remain failures. The input files are unchanged. This follows the distinction in
the [DICOM SOP Instance Reference Macro](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_10.8.html)
between the referenced object's class and its instance identity; it does not
assert equivalence between standard and private plan classes. Privacy and
clinical-completeness limitations remain unchanged.

## Machine and beam text

Version 0.2.0 defaults to `text_policy="clinical"`: **Preserve clinical names
and descriptions** in the GUI. It retains an explicit public RT text allowlist
at every retained sequence depth. This includes `ROIName`, `ROIDescription`,
`BeamName`, `BeamDescription`, plan and structure-set labels, prescription and
dose-reference descriptions, `PrescriptionNotes`, and selected treatment,
setup, accessory and material labels. The complete field list is in
[`RT_TEXT_KEYWORDS`](../src/opendicom_deid/core/rt_text.py). This is not a rule
to retain arbitrary text merely because it appears in an RT object.

Before transforming files, the engine gathers identifiers across the complete
batch: all public person-name (`PN`) fields, including patient and staff names;
selected patient, study, accession and clinical-trial identifiers; entity and
reference UIDs; institution names, addresses and department names; station and
serial identifiers; and date/date-time (`DA`/`DT`) values. Name components and
selected distinctive organization-name components are included. These values
stay in memory and are not written to reports.

Clinical text is normalized for case, punctuation and diacritics for matching.
Matching source spans are removed from the original text while unrelated
clinical words remain. A fully removed or punctuation-only result becomes
`ANONYMIZED`. Longer tokens can match within words; short identifiers generally
need delimited components. A short numeric `StudyID` must match the whole field
to avoid incidental matches in version numbers. Screening can also remove a
clinical substring that happens to match a known identifier: review altered
labels before downstream use.

The optional `text_policy="strict"` (**Scrub clinical names and descriptions**;
CLI `--text-policy strict`) uses the previous broad text scrubbing for those
fields. Beam names become `BEAM-<BeamNumber>`, or a compact `B<number>` for long
numbers. Empty names stay empty; names without a usable beam number become
`ANONYMIZED`. Strict mode keeps numeric parameters, references and retained
sequence structure, including prescription-note sequence item structure while
scrubbing its text. It does not disable the equipment exceptions below.

In both modes, the six equipment fields introduced in 0.1.6 remain explicitly
allowed: `Manufacturer`, `ManufacturerModelName`, `SoftwareVersions`,
`TreatmentMachineName`, `FluenceModeID`, and `MaterialID`. If any known identifier
matches, the whole affected equipment value becomes `ANONYMIZED`. Unlike the
clinical span policy, it does not construct a partially changed machine/model
identifier. Beam numbers and references, control points, meterset weights,
geometry and MLC arrays retain their numerical and relationship checks.

The independent output verifier compares exact expected equipment text in both
modes, exact expected clinical RT text in clinical mode, and the appropriate
beam-label transformation. Retained RT sequence item counts and selected
numeric parameters are compared at their sequence paths. A warning records
retained text requiring review; another records known-identifier removal without
revealing the removed values.

These checks cannot identify arbitrary names, aliases, identifiers absent from
the known source fields, alternate date spellings or every encoding. Dedicated
identifier fields are still removed or pseudonymized, but preserving useful
free text does not prove that it is anonymous. Review retained labels and notes
locally; device identity can also link a dataset to a particular site. Strict
mode reduces retained clinical text but does not replace a privacy review.

The [DICOM RT Beams module](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.8.14.html)
distinguishes beam numbers, user-defined names, and equipment parameters. The
[Retain Device Identity Option](https://dicom.nema.org/medical/dicom/current/output/chtml/part15/sect_E.3.8.html)
recognizes both the analytical value and privacy implications of retaining device
information. This narrower application policy does not implement or claim
conformance to that option: serial numbers and other identifiers outside the
explicit text exceptions remain scrubbed. It also does not establish clinical
completeness or suitability for independent dose reconstruction.

Reprocess the original export when changing policy. Names already replaced by
`ANONYMIZED` cannot be recovered. Slicer reader and database problems are
separate from text retention; see [the Slicer guide](slicer.md). The application
does not invent missing contours, constraints or treatment-system export data.

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

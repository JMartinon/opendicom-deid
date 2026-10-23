# Validation evidence and remaining acceptance work — 0.2.3

This document describes implementation evidence, not certification or clinical
acceptance. All automated test objects are generated in temporary directories
from synthetic values. No patient dataset is included in the repository or test
fixtures. Limited read-only troubleshooting of an already anonymized export is
separate from the automated evidence and does not constitute clinical acceptance.

## Radiotherapy regression

`tests/test_rt_references.py` constructs 661 interconnected objects: 198 CT,
450 MR, 11 RTDOSE, one RTPLAN with 21 beams, and one RTSTRUCT with 31 ROIs.
The sources occupy nested image, plan, and repeated dose-run directories.

Before processing, the test establishes that all 255 inspected instance
references resolve: 241 in RTSTRUCT, three in RTPLAN, and 11 in RTDOSE. After
processing, every reference resolves to its consistently remapped target. The
test also checks instance counts, patient/study/frame linkage, contour numbers,
beam parameters, geometry, and exact pixel bytes.

The test then deliberately restores all 255 original nested references while
leaving the replacement instance UIDs in place. All files remain parsable, but
the independent validator rejects the output and emits no reference-integrity
PASS. This reproduces the reported failure mode without claiming to reproduce
every vendor-specific characteristic of the original files.

`tests/test_rt_storage.py` adds a linked CT/MR/RTSTRUCT/RTPLAN/RTDOSE/RTIMAGE/
RT Ion Plan batch. It checks eight nested instance references, unchanged storage
class UIDs and pixel bytes, contours, beam/control-point values, and ion scan-spot
values. This demonstrates conventional RT support; it does not establish support
for every proprietary or newer RT storage class used by a vendor.

An `UNSUPPORTED_OBJECT` finding means that the actual SOP Class UID is outside
the supported set, even if a viewer labels the file RTPLAN or RTIMAGE. Reports
now identify recognized standard storage classes by name and public technical
UID. Unknown/private identifiers remain hidden in ordinary reports. These
objects still block publication until their handling has been verified.

The report's `input_count` and modality counts describe accepted scan records;
rejected objects are additional. A report with 650 accepted objects and one
unsupported-object failure therefore does not mean only 650 DICOM files exist.

### ViewRay private RT plan research export

Local inspection supplied by the user identified the exact class
`2.16.840.1.114493.1.2.1.4.1.1.481.5`, `Modality=RTPLAN`, and Implicit VR Little
Endian. The previous standard-only class list caused the reported rejection.
The user identified the exporting system as MRIdian A3i (UDI-PI) **5.5.4.14**;
this records the reported environment, not a tested compatibility claim.
No original patient dataset was used to develop the initial class support.
No public vendor specification for this class was located, so its similarity to a standard UID is not treated
as proof of equivalence. [DICOM PS3.5 Section 9](https://dicom.nema.org/medical/dicom/current/output/chtml/part05/chapter_9.html)
prohibits inferring semantics from a UID's suffix.

The vendor's [version 5.5 operator manual, L-0240 RevA, printed page 521](https://viewraysystems.com/wp-content/uploads/L-0240-Operators-Manual-for-the-MRIdian-Linac-System-Version-5.5_RevA.pdf#page=531)
documents that **Export MRIdian RT Plan** is available after selecting **Export
RT Plan**. It does not specify the resulting SOP classes, the private attributes,
or whether the option adds or replaces an object. Unchecking it is therefore not
a verified way to obtain a standard plan or avoid this rejection. A matching
DICOM conformance statement and export specification would help establish which
reconstruction inputs the export provides. A private SOP class does not itself
prove that private attributes are present in a particular file; private fields,
when present, need their own documented handling policy.

Version 0.1.4 recognizes only this exact private class and offers an explicit,
default-off **ViewRay public-data research export** option. It keeps the class
UID unchanged; class references are preserved except for the narrowly scoped
correction added in version 0.1.7 below. Existing private-tag removal still applies,
so proprietary treatment information may be lost; complete MRIdian plan
preservation and treatment-system re-import are unsupported. Both batch reports
and standalone validation carry this limitation. Recognition is not full vendor
conformance, and no rewrite of the plan's own class or broad vendor-prefix
exception is used.

The public-data guard requires nonempty beams with unique positive beam numbers,
at least two control points per beam, matching counts and sequential indexes,
finite nondecreasing meterset weights from zero to a positive final weight, and
readable public geometry in the first control point. Later control points may
inherit omitted geometry. An explicitly supplied final meterset weight must
match. These checks use selected public [RT Beams semantics](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.8.14.html),
not an exhaustive private or standard IOD validator. They reject plans without
the required public content and the inconsistencies listed above. They do not
establish completeness: for example, acceptance does not require fraction-group
beam metersets or a complete MLC description.

Independent dose reconstruction is not validated. The equipment-field exceptions
introduced in 0.1.6 still apply. Version 0.2.0 also retains curated clinical RT
labels and descriptions by default, with known-identifier spans removed. Saved
output is compared with the exact expected text transformation; optional strict
mode scrubs the clinical text. All private attributes are removed. Supporting
reconstruction requires defined inputs for the intended calculation workflow,
appropriate vendor documentation, and validation that the relevant clinical
information survives de-identification. Selected preservation checks alone do
not establish completeness.

`tests/test_viewray_plan.py` builds synthetic linked CT/MR/RTSTRUCT/ViewRay-plan/
RTDOSE/RTIMAGE objects using Implicit VR Little Endian. It checks unchanged
storage-class UIDs, remapped instance references, public beam/MLC geometry,
meterset weights and pixels, root/nested private removal, and the malformed
private FD case. Negative tests cover default rejection, neighboring unsupported
UIDs, modality/file-meta mismatches, missing public treatment content, malformed
control points, and deliberately changed output. Numeric RT reference values
and retained plan/reference sequence cardinalities are fingerprinted as well.
This establishes behavior on synthetic fixtures, not acceptance of the user's
actual plan or the completeness of its exported public data.

### ViewRay reference-class mismatch handling

A version 0.1.6 report showed `REFERENCE_CLASS_MISMATCH`: a reference named a
class different from its selected target's actual class. The report did not
identify the class pair or reference location, so the specific cause in the
user's original export has not been confirmed. No original dataset was used to
develop or verify this correction.

Version 0.1.7 checks reference classes during scanning and provides neutral file
numbers, numeric sequence/tag locations and recognized class names for remaining
mismatches. It does not expose source paths, entity UIDs or unknown raw class
UIDs. A reference to an object outside the input remains an explicit missing
reference warning; there is no target class to infer or repair in that case.

With the ViewRay research option enabled, one narrowly defined mismatch can be
corrected: standard `RTPlanStorage` in `ReferencedSOPClassUID` paired with a
single instance reference that resolves to the exact supported private ViewRay
RTPLAN in the same patient group. Resolution must be unambiguous. The class
field is changed to the selected target's actual class and a
`VIEWRAY_REFERENCE_CLASS_REPAIR` warning records the correction. Original files,
the plan's own class and the entity-UID remapping policy remain unchanged. The
[DICOM SOP Instance Reference Macro](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_10.8.html)
defines these as separate class and instance attributes; the correction does not
convert a private plan into a standard plan.

Missing targets, reverse or unrelated class pairs, ambiguous conflicting
duplicates and cross-patient references are not corrected. The validator
recomputes expected corrections from the original sources and verifies the exact
reference-class locations in the output. It continues to reject additional
class changes, altered instance references and changes to the plan's own class.
Passing these checks does not establish real ViewRay compatibility or complete
inputs for independent dose reconstruction.

## Other automated checks

### Appearance and stage progress

Version 0.2.3 follows OS light/dark appearance without an app preference or
switch. `tests/test_appearance.py` covers macOS native/fallback queries, Windows
application appearance, Linux portal/KDE/GTK fallbacks, bounded local commands,
and background watcher updates, failures and shutdown. Detection does not write
settings or use a network service. `tests/test_theme.py` checks contrast for
text, progress and scrollbar thumbs in both palettes.

`tests/test_progress.py` checks stage percentages, completion counting for
skipped/rejected scan files, bounded latest-update storage, the final source
recheck, and cancellation before publication. The percentage measures files
completed in that stage, not elapsed time or total job completion. Unknown-length
steps use an animated indicator rather than a fabricated percentage.

The GUI smoke check holds a synthetic worker between stages to inspect the
painted progress element and displayed counts. It also simulates OS appearance changes during
processing and checks that the job remains active and its progress is preserved.
Packaged startup checks exercise both palettes and a visibly filled progress
element. The scrollbar smoke check exercises native dragging, page movement,
resizing, automatically hidden tracks and unchanged read-only text. These checks
do not replace visual acceptance on each target platform.

### Clinical text, equipment fields and strict mode

Version 0.2.0 defaults to preserving an explicit list of public RT names,
descriptions and prescription notes. Source-known identifier spans are removed,
retaining unrelated clinical words. The batch screening set includes patient
and staff person-name fields and components, selected patient/study identifiers,
entity/reference UIDs, institution information, serial numbers, and DA/DT dates.
Normalization handles case, punctuation and diacritics. The optional strict
policy restores broader clinical-text scrubbing and neutral beam labels.

The six equipment exceptions introduced in 0.1.6 remain in both modes:
`Manufacturer`, `ManufacturerModelName`, `SoftwareVersions`,
`TreatmentMachineName`, `FluenceModeID`, and `MaterialID`. An identifier match
neutralizes the whole equipment value rather than deleting a substring from a
machine/model identifier. Short numeric `StudyID` values require a whole-field
match to avoid software-version collisions.

`tests/test_rt_text.py` exercises retained ROI/beam/plan labels, clinical
descriptions, prescription notes and numeric constraints; removal of patient,
staff and institution identifiers; cross-file screening; Unicode matching;
and strict-mode behavior. Linked objects verify that contour coordinates,
numeric beam data, prescription numbers and references remain consistent.
Adversarial output changes to clinical text, prescription numbers and retained
RT sequence item counts and coded constraint types must fail validation. The standalone validator reads
the saved text-policy marker, but cannot prove source-to-output equivalence
without the source snapshots.

Source-to-output verification compares exact expected equipment text in both
modes, clinical RT text in clinical mode, and the selected beam-label
transformation at their sequence locations. It also compares selected numeric
metadata, pixels and retained RT sequence item counts. This is preservation
evidence for the implemented scope, not a privacy clearance or proof of complete
dose-reconstruction inputs. Unknown identities can remain in free text, and
coincidental matches can alter a useful label. See the
[machine and beam text policy](confidentiality.md#machine-and-beam-text).

### Read-only identifier screening

Version 0.1.5 adds **Inspect file… → Check identifiers** and the equivalent
`inspect-privacy` CLI command. It reads the selected original file without the
anonymizer's private-tag removal. Findings identify populated common identifier
fields, person names, dates/times, free text, original entity/reference UIDs, and
private or opaque content requiring local review. Standard dictionary keywords,
tag paths and counts are returned; original field values, private creator names,
file paths, filenames, and entity UIDs are never included in the report.

Known PatientName/PatientID values are used in memory for exact byte matching
against loaded private values. Candidate tokens are length-limited, with UTF-16
forms added when the source bytes decode as UTF-8. Matching is incomplete and
does not decode arbitrary proprietary formats. A populated field or byte match
is a review candidate, not proof of a real patient identity. Absence of matches
does not establish anonymity. Completed inspection always returns
`REVIEW_REQUIRED`; the de-identification policy is unchanged.

The screen refuses files over 64 MiB and deflated datasets, defers individual
values over 1 MiB, and limits traversal depth, sequence items, element counts,
and returned findings. Limit hits, unreadable fields and parser diagnostics
remain explicit. These traversal limits do not prevent pydicom's earlier eager
sequence parsing. Pixels, metadata after pixels, and compressed/encrypted or
unknown content are outside the screening claim.

Synthetic tests cover nested identifying fields, private bytes/strings/sequences,
malformed private FD, UTF-16 matches, source immutability, diagnostic suppression,
all inspection limits, incomplete and cancelled results, file numbering, and CLI
exit semantics. Source and packaged GUI smoke checks exercise the new button and
value-free results; source GUI checks also verify copying and cancellation.

### Secondary Capture source modalities

The TCIA evaluation collection includes 57 mammography images stored using
Secondary Capture Image Storage (`Modality=MG`). The earlier scanner incorrectly
rejected this combination. Secondary Capture Modality is optional and describes
the source of the captured image, as specified by
[DICOM PS3.3 C.8.6.1](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.6.html).
Both scanning and saved-output validation now accept these semantics for all
five supported Secondary Capture storage classes. Pixel requirements are based
on the storage class; a capture labelled RTPLAN cannot bypass image checks.

Synthetic regressions cover MG and RF source modalities, optional absent/empty
values, supported single/multi-frame encodings, unchanged pixel bytes and
modality values, malformed/multivalued codes, and safe labels for extension
codes. Repeated findings are grouped in the text summary; JSON retains every
individual finding.

The compatibility investigation queried a representative instance's metadata
from each of the evaluation collection's 26 series using TCIA's public API.
No image pixels or complete DICOM files were downloaded. This established the
SOP-class/modality mismatch, **not full processing or privacy validation of the
collection**. All regression fixtures remain programmatically generated.

Dataset attribution: Rutherford, M., et al. (2021), *A DICOM dataset for
evaluation of medical image de-identification (Pseudo-PHI-DICOM-Data)*,
The Cancer Imaging Archive, [doi:10.7937/s17z-r072](https://doi.org/10.7937/s17z-r072),
CC BY 4.0. [Collection and access details](https://www.cancerimagingarchive.net/collection/pseudo-phi-dicom-data/).

### Integrity and privacy checks

The suite covers CT, MR, RTIMAGE, grid and legacy non-grid RTDOSE; patient identity
and issuer grouping; multiple studies; shared Study/Series/Frame of Reference
UIDs; deeply nested and multivalued UIDs; preserved technical identifiers;
identical copies and conflicting duplicates; external references; consistent
date shifts; source grouping; cancellations and partial failures; protected
destinations; malformed metadata; and reports that exclude source identities.

Output organization regressions exercise nested patient/study folders, repeated
folder names, duplicate instances in different subfolders, and several patients
sharing a source folder. Default output preserves neutral directory ancestry
and separates patients; explicit flat output is also checked. Cancellation and
write failures must discard incomplete nested output without changing input or
populating an existing empty destination.

GUI smoke checks verify that choosing input, editing the path, changing focus,
and toggling recursion start no background scan. The explicit scan and
De-identify buttons are exercised with a generated CT object. On Linux,
`scripts/check_folder_dialog.py` uses actual X11 mouse press/release events to
check that the path dropdown stays open, an ancestor can be selected, and
cancellation restores both ordinary and nested modal dialogs correctly.

Desktop format inspection uses the same header-only API as the local source
helper. Synthetic GUI checks exercise unsupported standard and private storage
classes, explicit private-class-ID opt-in, clipboard output, stale-result
clearing, and unchanged input files. Unit tests also cover cancellation without
partial results, recursion, invalid file numbers, and safe parser errors. This
diagnostic helps identify an unsupported object; it does not add storage-class
support or establish real ViewRay compatibility.

The ViewRay private FD-length regression is tested by inserting the six-byte
value `b'0.938 '` at `(01F1,1026)`, both at the root and inside a sequence. The
reader removes raw private elements before conversion. It never enables
`convert_wrong_length_to_UN` globally. Wrong public value representations and
malformed clinical fields fail, rather than being silently reinterpreted.

Adversarial validation tests change pixels, geometry, dates, pseudonyms,
technical UIDs, references, file meta, and the preamble; add private metadata;
or remove output files. These cases must fail the relevant checks. Compressed
pixel encapsulation is checked for complete nonempty fragments and valid basic
offset-table targets. Compressed images are **not decoded**: successful byte
preservation is not evidence that an arbitrary compressed stream can be displayed.

## Scope of the checks

- Readability means strict metadata parsing, public value-representation checks,
  and selected structural checks. It is not exhaustive DICOM IOD conformance.
- Clinical preservation compares selected numeric metadata, byte fingerprints
  and retained RT sequence item counts at their sequence paths, plus the exact
  expected equipment/clinical text and beam-label transformation for the selected
  policy. It does not verify treatment-plan clinical correctness or completeness.
- Byte-identical copies of an instance share their replacement UID. Non-identical
  files with the same original instance UID are rejected conservatively, even
  if the difference might only be a harmless alternate representation.
- Missing patient IDs are grouped by study with an explicit warning; cross-study
  linkage cannot be inferred reliably. Conflicting demographics under the same
  scoped ID fail rather than silently combining patients.
- Legacy point/isodose RTDOSE content is preserved without requiring image-grid
  fields. Those modules are retired in the current standard; DVH-only objects
  also need no grid. See the [current RT Dose module table](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_A.18.3.html).
- Unknown public attributes, unknown UID semantics, unrecognized private SOP classes, and
  unsupported storage classes require specialized handling. An explicit failure
  protects against an unsupported object being presented as successfully cleaned.
- MPEG/HEVC video transfer syntaxes are unsupported. Their frame encapsulation
  and pixel privacy requirements need separate validation.
- Only a completed directory rename publishes the output. A forced process kill,
  crash, power loss, or failed cleanup can leave a hidden `.opendicom-incomplete-*`
  directory beside the destination. It must not be shared as a completed result.

## Local and platform acceptance

For version 0.2.3 on October 23, 2026, **519 tests passed** locally on Python
3.12.14 with Tcl/Tk. On Python 3.14.7 without Tcl/Tk, **517 tests passed** and the
two-test palette module was skipped as a whole. Ruff reported no errors.
Native macOS GUI checks passed for automatic appearance changes, stage progress,
cancellation and scrollbar dragging, paging, resizing and automatic hiding.
The locally packaged macOS ARM64 application passed native appearance and frozen
startup checks, plus synthetic equipment/clinical text, ViewRay opt-in, reference
and pixel checks. De-identification policies are unchanged in this release.
Native Windows and Linux 0.2.3 verification remains outstanding; no hosted
Actions builds were run for this update.

For version 0.2.2 on October 23, 2026, **482 tests passed** locally on Python
3.12.14 with Tcl/Tk. On Python 3.14.7 without Tcl/Tk, **464 tests passed** and the
18-test appearance module was skipped as a whole. Ruff reported no errors.
Native macOS GUI checks cover both palettes, a visibly filled progress bar,
stage changes, cancellation, a completion/progress race, and restarting the
indeterminate animation. These checks use synthetic inputs.
The locally packaged macOS ARM64 application passed frozen GUI startup and
synthetic equipment/clinical text, ViewRay opt-in, reference and pixel checks.
Native Windows 0.2.2 verification remains outstanding.

For version 0.2.1 on October 23, 2026, all **439 tests passed** locally on Python
3.12.14 and 3.14.7; Ruff reported no errors. `tests/test_windows_file_access.py`
adds 16 synthetic checks
for bounded Windows lock retries, persistent errors, cancellation, privacy-safe
diagnostics, no replay of partial writes, and source/destination rechecks before
publication. These inject numeric Win32 errors on local test hosts; they do not
replace native Windows verification. A user-reported 0.2.0 Windows build first
failed in synthetic processing, then succeeded on a complete rerun. The original
generic error did not identify the failing operation or establish the cause.

For version 0.2.0 on October 23, 2026, all **423 tests passed** locally on
Python 3.12.14 and Python 3.14.7; Ruff reported no errors. The suite includes
clinical/strict text policies, identifier screening, prescription constraints,
RT sequence and coded-value preservation, and backward-compatible standalone
validation of the exact legacy v2 strict-policy marker. Unknown or mixed markers
are rejected. The GUI smoke check exercises both text modes and their option
forwarding using synthetic data.

For version 0.1.7 on October 21, 2026, all **349 tests passed** locally on both
Python 3.12.14 and Python 3.14.7. Ruff reported no errors. That suite included the full 661-object
regression described above, strict parser failures, transactional cleanup, and
an isolated process with socket connections blocked. Forty equipment-text tests
cover retained values, neutral beam labels, cross-file identifier matches,
short identifiers, and deliberate output tampering. Twenty-two reference-class
tests cover the narrow correction, identity scope, duplicate targets, safe
diagnostics, unchanged source content, and output tampering. Local GUI smoke
checks passed. These earlier release results are historical evidence, not
test counts for 0.2.3.

Builds now run locally by default. The **Tests**, **Native desktop builds** and
**Draft versioned release** workflows require deliberate manual initiation;
pushes, pull requests and tags no longer spend hosted minutes automatically.
The optional native matrix still covers Windows x86-64, macOS ARM64/Intel and
Ubuntu x86-64. Consult actual test/build results; configuration alone is not
evidence that an artifact works.

The local Linux workstation uses Ubuntu 26.04 and glibc 2.43. Its native artifact
must not be assumed compatible with an older distribution. The optional Ubuntu
24.04 hosted build has an older baseline; Linux system libraries are not fully
bundled. Windows uses a native local build through `scripts/build_windows.cmd`,
with an isolated Python 3.12+ environment and Tcl/Tk. See
[distribution and local-build instructions](distribution.md).

### Slicer troubleshooting evidence

Read-only investigation separated the reported failures into the current
SlicerRT reader's unsupported private plan class, a legacy database-setting
lookup, and empty contour entries. It did not change clinical files or prove
that the original export contained those empty entries. It also did not validate
private-plan beam loading or dose reconstruction. See [the Slicer guide](slicer.md)
for reader selection and the reversible database-setting workaround.

The compatibility helper has 15 focused tests for validating an open database,
preserving the original setting through repeated application, restoring missing
and existing values, refusing incomplete backups and suppressing exception
details. On October 23, 2026, the installed Slicer 5.13 also passed a runtime
smoke check with temporary settings and an empty temporary database. Existing
user settings and patient files were untouched. This verifies the helper's
actual Qt/CTK interfaces, not SlicerRT clinical import acceptance.

Version 0.2.2 adds 12 tests for the session-only Scalar Volume reader guard,
including mixed lists, cached loadables, existing plugin instances, restoration,
and unavailable databases. The installed Slicer 5.13 passed a synthetic runtime
check: the known private plan was excluded, a CT volume loaded through GDCM,
and the original reader was restored. The combined startup helper was also
verified with temporary settings and a temporary database. These tests do not
establish that SlicerRT supports private-plan beams.

Clinical labels scrubbed by older releases cannot be reconstructed from their
output. Rerun the original export with the new text policy before assessing
structure and plan labels in the intended viewer.

Before distributing this initial release, open the extracted artifact on a clean
machine without Python, test the synthetic RT outputs in the intended viewer,
and check representative authorized datasets locally. Full Slicer/SlicerRT and
ViewRay clinical acceptance remains outstanding. Every dataset still
needs privacy review, including pixels, coded/free text, and recognizable anatomy.

# Validation evidence and remaining acceptance work

This document describes implementation evidence, not certification or clinical
acceptance. All test objects are generated in temporary directories from
synthetic values. No original ViewRay or other patient dataset was used.

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
No patient dataset was transferred or used here. No public vendor specification
for this class was located, so its similarity to a standard UID is not treated
as proof of equivalence. [DICOM PS3.5 Section 9](https://dicom.nema.org/medical/dicom/current/output/chtml/part05/chapter_9.html)
prohibits inferring semantics from a UID's suffix.

Version 0.1.4 recognizes only this exact private class and offers an explicit,
default-off **ViewRay public-data research export** option. It keeps the class
UID and class references unchanged. Existing private-tag removal still applies,
so proprietary treatment information may be lost; complete MRIdian plan
preservation and treatment-system re-import are unsupported. Both batch reports
and standalone validation carry this limitation. Recognition is not full vendor
conformance, and no class rewrite or broad vendor-prefix exception is used.

The public-data guard requires nonempty beams with unique positive beam numbers,
at least two control points per beam, matching counts and sequential indexes,
finite nondecreasing meterset weights from zero to a positive final weight, and
readable public geometry in the first control point. Later control points may
inherit omitted geometry. An explicitly supplied final meterset weight must
match. These checks use selected public [RT Beams semantics](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.8.14.html),
not an exhaustive private or standard IOD validator. They intentionally reject
private-only and incomplete public representations.

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

## Other automated checks

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
- Clinical preservation compares selected numeric metadata and byte fingerprints
  at their sequence paths. It does not verify treatment-plan clinical correctness.
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

On October 21, 2026, all **247 tests passed** locally on both Python 3.12.14 and
Python 3.14.7. Ruff reported no errors. The suite includes the full 661-object
regression described above, strict parser failures, transactional cleanup, and
an isolated process with socket connections blocked.

Local validation is performed on Apple Silicon macOS. Native GitHub Actions
workflows provide separate Windows x86-64, macOS ARM64/Intel, and Ubuntu x86-64
test/build jobs. Consult their actual results; workflow configuration alone is
not evidence that an artifact works.

Before distributing this initial release, open the extracted artifact on a clean
machine without Python, test the synthetic RT outputs in the intended viewer,
and check representative authorized datasets locally. Slicer/SlicerRT and real
ViewRay acceptance testing have not been performed here. Every dataset still
needs privacy review, including pixels, coded/free text, and recognizable anatomy.

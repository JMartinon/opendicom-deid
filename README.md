# OpenDICOM De-ID

Simple, offline, cross-platform DICOM de-identification.

OpenDICOM De-ID is a Python desktop application for de-identifying batches of
DICOM files while preserving relationships between images and radiotherapy
objects. It works locally, with no account, telemetry, PACS connection, or data
upload. The processing engine also has a command-line interface.

**Version 0.2.0 is an initial validation release.** Automated checks do not prove
that all identifying information has been removed, establish clinical
suitability, or demonstrate regulatory compliance. Review every dataset before
sharing it. No claim of a fully verified DICOM confidentiality profile is made.

## What it does

- Recursively discovers DICOM files and shows counts by modality.
- Builds one UID mapping for the complete batch, including arbitrarily nested
  references, while retaining technical UIDs such as SOP Class and Transfer Syntax,
  except for the narrowly scoped ViewRay reference correction described below.
- Assigns different patient pseudonyms within a batch and uses each patient's
  pseudonym consistently across studies.
- Removes private metadata by default; offers date removal or consistent date
  shifting and preserves nested source folders with neutral names by default.
- Preserves selected RT structure, beam, plan and prescription text by default,
  removing spans that match known patient, staff or institution identifiers.
  An optional strict text policy scrubs those clinical labels and descriptions.
- Preserves selected manufacturer, model, software, machine, material and
  fluence-mode text after screening it against known identifiers in the batch.
- Compares input/output references, pixel bytes, geometry, and selected RT
  parameters. Pre-existing external references produce explicit findings.
- Rejects conflicting instance duplicates and unsafe input/output locations.
- Publishes output only after mandatory checks; cancellation or failure does not
  present partial output as a completed dataset.
- Saves reports without original patient values, source paths, or UID mappings.

The application uses pydicom and selected rules from Kitware's
[dicom-anonymizer](https://github.com/KitwareMedical/dicom-anonymizer), with its own
UID traversal and validation. See [the implemented policy and limitations](docs/confidentiality.md)
and [architecture](docs/architecture.md). [Validation evidence](docs/validation.md)
describes the synthetic regression and the remaining acceptance work.

## Desktop workflow

![Illustration of the desktop workflow using a synthetic dataset](docs/images/workflow.svg)

The illustration uses synthetic counts; it is not a clinical validation result.

1. Choose the input directory. Selecting or editing a path does not start a scan.
   **Scan input** is an optional preview of file counts, modalities, and findings.
2. Choose a **new or empty** output directory, outside the input directory tree.
   Use **New folder…** beside the output field to create and select a folder.
3. Leave the defaults in place, or expand **Advanced options**.
4. Click **De-identify**. It scans, processes, and validates the dataset in one run.
   You can cancel while the interface remains responsive.
5. Read the findings, save a report if needed, and perform a privacy review.

Private-tag removal and validation are always enabled. A custom prefix must be a
neutral label; do not use a patient name or original identifier. Output preserves
the source folder hierarchy by default, replacing each folder name with a label
such as `group-0001`. Original folder names are never copied because they can
contain patient information. Patient folders and nested study/series or dose-run
folders therefore remain separate. If a source folder directly contains files
from several patients, those files are further separated into folders named with
their generated patient pseudonyms. Empty folders and non-DICOM files are not copied.
Turn off **Preserve patient folders and subfolders** in Advanced options only if
you want a flat output directory (CLI: `--organization flat`).

### Preserve useful RT information

Version 0.2.0 defaults to **Preserve clinical names and descriptions** under
**Advanced options → RT information**. A curated set of public RT fields keeps
ROI names, beam names, plan labels, prescription descriptions and notes, and
selected treatment/accessory labels. Numeric prescription constraints, contour
coordinates, beam/control-point parameters and references retain their
preservation checks. This does not recover information absent from the export.

Patient identifiers, birth dates, staff names, hospital details and serial
numbers remain removed or replaced in their dedicated metadata fields. Before
retaining clinical text, the engine removes spans matching known identifiers
from the entire batch, including names in person-name fields, identifiers and
entity UIDs, institution details, serial numbers and dates. Unmatched clinical
words remain. Names and identifiers absent from those source fields can survive,
so retained text still needs local review.

Choose **Scrub clinical names and descriptions** for the more restrictive text
policy (CLI: `--text-policy strict`). It replaces or removes those clinical
labels and descriptions and generates neutral beam names such as `BEAM-1`.
Both policies retain the six screened equipment fields introduced in 0.1.6:
`Manufacturer`, `ManufacturerModelName`, `SoftwareVersions`,
`TreatmentMachineName`, `FluenceModeID`, and `MaterialID`. A known-identifier
match neutralizes the entire equipment value. See the
[text policy and its limits](docs/confidentiality.md#machine-and-beam-text).

**Rerun from the original export** to recover meaningful labels after an older
version replaced them with `ANONYMIZED`; the removed names cannot be reconstructed
from that output. For Slicer database errors, private-plan reader selection and
empty-contour warnings, see [Importing into 3D Slicer](docs/slicer.md).

### ViewRay private RT plans

To check an original plan for potential identifying metadata without changing
it, use **Inspect file… → Check identifiers**, described below. This is separate
from the optional export mode.

The observed ViewRay RTPLAN class
`2.16.840.1.114493.1.2.1.4.1.1.481.5` has an explicit **public-data research export**
mode. In version 0.1.4 or newer, open **Advanced options**, enable **ViewRay
public-data research export**, then run **De-identify** on the complete input
tree. This setting is off by default because proprietary treatment fields are
removed, and a full MRIdian plan or treatment-system re-import is unsupported.
**This mode is not validated for independent dose reconstruction.** The selected
public equipment text and curated clinical RT text follow the selected text
policy described above. Other free text and all private attributes remain
subject to removal or scrubbing. Keep the original
export in its authorized clinical storage if future reconstruction is required.

The mode requires public beam/control-point data with coherent numbers, counts,
indexes, geometry, and meterset weights. It preserves the original private class
UID in the plan and file meta; remaps entity UIDs; and checks retained public RT
values and relationships. Class references are preserved except for the specific
correction described below. It does **not** convert the file to standard
RT Plan Storage. A viewer that does not support the original private class may
still refuse the result. Plans without the required public beams/control points,
or with inconsistencies covered by these selected checks, fail. Passing these
checks does not establish that all inputs for dose calculation are present.

Version 0.1.7 can correct one type of inconsistent reference when this mode is enabled:
a reference that names standard **RT Plan Storage** but points by instance UID to
the supported private ViewRay plan in the selected input. The target must be
unambiguous and belong to the same patient group. Only the reference's class UID
is changed to match the actual target, with a `VIEWRAY_REFERENCE_CLASS_REPAIR`
warning. The plan's own class and the instance-UID mapping policy are unchanged, and
source files are never modified. Missing targets, conflicting duplicates,
cross-patient references, reverse mismatches and other class pairs are not
corrected. Remaining class mismatches fail during scanning, with neutral file
numbers and tag locations to help local investigation.

Every accepted plan produces a warning about removal of proprietary fields.
This is not verified full ViewRay support: no public specification for the class
or complete clinical acceptance evidence is available. All other unknown/private
storage classes remain unsupported. See [scope and evidence](docs/validation.md#viewray-private-rt-plan-research-export).

CLI equivalent (for public-data research only):

```sh
opendicom-deid deidentify /path/to/input /path/to/output --viewray-public-plan
```

Reports use a new `.json` filename and never overwrite an existing file. Saving
inside the selected input tree is prohibited. Pseudonym and UID mappings exist
only in memory; this version offers no reversible identity export.

### Check an original file for identifying information

In version 0.1.5 or newer, choose the same input folder and subfolder setting as
the failed run, click **Inspect file…**, enter the report's file number (for
example, `199` for `File 000199`), then click **Check identifiers**. No output
folder is needed. It reads the original file without removing private fields,
changing files, or uploading data.

The report lists populated identifying fields, other names/dates/free text,
original entity UIDs, and private or opaque content requiring review. It can
also flag occurrences of known patient identifiers in readable private values.
Only tag locations, standard field names, and counts appear in the report;
patient values, source paths, and original UIDs are omitted. Use **Copy report**
to share those findings for troubleshooting.

**No matches does not mean anonymous.** Compressed, encoded, unknown, oversized,
unreadable, or uninspected content can still contain identifiers. The check does
not inspect pixels and never gives privacy clearance. The result remains
`REVIEW_REQUIRED`. A populated field may contain a pseudonym or dummy value;
the check cannot decide that from its location alone. Review values locally in
an authorized DICOM viewer when necessary, including nested sequences and
private fields, without sharing raw dumps.

Even a plan without names or patient IDs cannot simply be copied into a
de-identified batch: study, instance, frame-of-reference, and nested reference
UIDs must remain consistent with the transformed images, structures, and dose.
This inspection does not change the anonymization or private-field retention policy.

CLI equivalent:

```sh
opendicom-deid inspect-privacy /path/to/input --file-id 199
```

Exit code zero means the inspection ran, not that privacy checks passed.

## Install a desktop build

Use the native archive supplied by the maintainer or a published
[release](https://github.com/JMartinon/opendicom-deid/releases). Builds are now
made locally by default to conserve GitHub Actions minutes. Existing or manually
requested [native build runs](https://github.com/JMartinon/opendicom-deid/actions/workflows/build.yml)
also offer platform packages under **Artifacts**. These packages include Python,
Tcl/Tk, the application, and dependency notices.
**End users do not need Python, a terminal, or a local build.**

For Windows, select **Windows-application**. Extract the downloaded ZIP once and
double-click **OpenDICOM-De-ID.exe** directly inside the extracted folder. The
standalone executable includes its runtime; keep the accompanying license
notices when distributing it. The separate **OpenDICOM-De-ID-Windows-x86_64**
artifact is the versioned archive plus checksum for distribution; that artifact
requires extracting both the outer GitHub ZIP and the inner application ZIP.

The repository is private, so GitHub downloads require signing in with an
account that has repository access. Actions artifacts are retained for **14
days**. For a published version, use the platform archive under **Assets** on the
[Releases page](https://github.com/JMartinon/opendicom-deid/releases); release
assets are not subject to Actions artifact expiry. A maintainer must publish a
release before it is available there to other repository readers.

| Platform | Distribution | Opening it |
| --- | --- | --- |
| Windows x86-64 | ZIP with standalone `.exe` | Extract, then double-click the executable |
| macOS Apple Silicon | ZIP with `.app` | Extract, then open the app |
| macOS Intel | Separate ZIP with `.app` | Extract the Intel build, then open the app |
| Ubuntu x86-64 | Portable `.tar.gz` folder | Extract the entire folder, then open its executable |

Packages are **not publisher-signed or notarized**. Windows and macOS can warn
about an unknown developer. Linux glibc and desktop libraries are not completely
bundled. The local Ubuntu 26.04 build uses glibc 2.43 and must not be assumed to
run on older distributions. The optional Ubuntu 24.04 hosted build has a different
runtime baseline; choose or build an artifact for the destination system. See
[distribution instructions](docs/distribution.md) for platform requirements,
security prompts, manual validation, and build limitations.

The repository starts **private**. A CI configuration is not evidence that a
platform build has passed. Review the actual workflow results and test each
downloaded artifact on a clean target machine before publishing a release.

## Develop locally

Use Python 3.12 or newer, with Tcl/Tk for the GUI. The CLI and tests can run
without Tcl/Tk. Official Python installers for Windows and macOS include Tk;
Linux distributions may require their `python3-tk` package. That package serves
the distribution's system Python, and does not add Tk to an unrelated custom
Python installation. See [Linux build setup](docs/distribution.md#linux-build-setup).

```sh
python3 -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev,build]'
python -m pytest
python -m ruff check .
python -m opendicom_deid
```

Only synthetic datasets are generated by the tests. Do not place real patient
data, identifying logs, mappings, or credentials in this repository.

The command-line interface exposes the same engine:

```sh
opendicom-deid scan /path/to/input
opendicom-deid deidentify /path/to/input /path/to/output --report /path/to/report.json
opendicom-deid deidentify /path/to/input /path/to/output --dates shift --organization source
opendicom-deid deidentify /path/to/input /path/to/output --text-policy strict
opendicom-deid validate /path/to/deidentified --report /path/to/validation.json
```

Exit codes: `0` for technical success (warnings may remain), `1` for failure,
`2` for invalid arguments/settings, and `130` for cooperative cancellation.
The independent `validate` command can inspect an output directory without the
original dataset, but it cannot establish original/output equivalence without
the in-memory batch comparison. Privacy review remains required in every case.

### Identify an unsupported storage format

If a report names a neutral file number such as **File 000199**, a local helper
can inspect that file's technical storage information automatically.

**Desktop app (Windows, macOS, or Linux; no Python or terminal needed):**

1. Open version 0.1.5 or newer on the computer containing the original data.
2. Select the same input folder and **Include subfolders** setting used in the failed run.
3. Click **Inspect file…**, enter **199**, and click **Inspect format**. If you first
   run **Scan input**, the first unsupported file number is filled in automatically.
4. If the storage class is unknown/private, check **Show vendor-specific storage
   class IDs in format details** and click **Inspect format** again.
5. Click **Copy report** to copy the small JSON result for troubleshooting.

No output folder is needed. This works offline on the computer holding the data;
the source dataset does not need to be moved. Nothing is copied to the clipboard
until you click the copy button. Private class IDs are revealed only on request.

**Source checkout:** from your Python environment, after `git pull --ff-only`, run:

```sh
python scripts/inspect_storage.py "/path/to/the/same/input-folder" --file-id 199
```

The source helper needs no application rebuild. For either method, keep the
input tree unchanged so the file number matches the report. Inspection reads selected header fields without
reading pixels, writing output, or printing filenames, patient fields, or
instance/study/series UIDs. If it reports an unknown/private storage class,
`--show-private-class-uids` reveals only that class's numeric UID for local
investigation. This inspection does not anonymize the dataset or change the
supported formats.

## Build native packages

**Build locally on the target operating system.** Pushes, pull requests and tags
do not automatically start hosted workflows. On Ubuntu 24.04 or newer, install the system
Python GUI support once, then build with that interpreter:

```sh
sudo apt-get update
sudo apt-get install python3-tk python3-venv libpython3-dev
PYTHON=/usr/bin/python3 bash scripts/build_linux.sh
```

The Linux wrapper checks Python/Tk before installing dependencies or running
tests. By default it tries `python3`, then `/usr/bin/python3` if needed. An
explicit `PYTHON` selection is used strictly. It reuses a compatible selected
virtual environment, or creates an isolated `.venv-build-linux` environment.
See [distribution instructions](docs/distribution.md#linux-build-setup) for
custom Python installations and headless build hosts.

On macOS, create an isolated environment with Python 3.12 or newer and working
Tcl/Tk, then build:

```sh
# macOS
python3 -m venv .venv-build
source .venv-build/bin/activate
bash scripts/build_macos.sh
```

On Windows, install 64-bit Python 3.12 or newer with Tcl/Tk and the Python
launcher, then double-click **`scripts\build_windows.cmd`** in the source
checkout. It creates an isolated `.venv-build-windows` environment, performs the
build and checks, and keeps the window open to show the result. See
[Windows build instructions](docs/distribution.md#windows-build-on-a-windows-computer)
for interpreter selection and prerequisites. Only the build computer needs Python.

The scripts install build dependencies, run tests, collect license notices, and
create a package and SHA-256 checksum in `dist/release/`. Build-time dependency
installation uses the network; the application itself performs no network
operations. Dependencies are pinned for DICOM behavior in `pyproject.toml`.

After a Linux build, start the application from the repository root with:

```sh
./dist/OpenDICOM-De-ID/OpenDICOM-De-ID
```

`dist/` contains the finished application; `build/` contains intermediate build
files. Keep the entire `dist/OpenDICOM-De-ID/` directory together, including
`_internal/`, which contains its runtime. For another computer, share the archive
in `dist/release/` and extract the whole archive before opening the application.
The build script prints the full launch and archive paths when it finishes.

The **Tests**, **Native desktop builds** and **Draft versioned release** workflows
are manually initiated. A deliberate draft-release run against a matching tag,
for example `v0.2.0`, builds the native packages and creates a draft prerelease;
creating or pushing the tag alone does neither. Hosted runs still consume
Actions minutes. No workflow changes repository visibility.

## Create the private GitHub repository

If the working directory has not yet been initialized or pushed, run:

```sh
git init -b main
git add .
git diff --cached --stat
git commit -m "Initial OpenDICOM De-ID implementation"
gh auth login
gh repo create opendicom-deid --private --source=. --remote=origin \
  --description 'Simple, offline, cross-platform DICOM de-identification.' --push
gh repo view --json nameWithOwner,visibility,url
```

Inspect the staged files before committing. Keep the repository private until
the application and distribution artifacts have been reviewed and validated.

## Limitations and privacy

- No pixel redaction, OCR, facial de-identification, or clinical transformation
  is performed. Burned-in text and recognizable anatomy can remain.
- Free text and unusual vendor-specific content need manual inspection. Private
  metadata removal can affect downstream vendor workflows.
- Unsupported object classes are reported; file readability alone does not
  establish safe de-identification or application compatibility.
- Batch consistency is not cross-run linkage. Separate runs deliberately create
  new identities and UIDs.
- Date shifting preserves intervals and may preserve identifying patterns.
- Original files must remain stable during processing. Large datasets can take
  time, and cancellation waits for a safe checkpoint.
- Synthetic regression tests reproduce nested RT reference failures. Limited
  Slicer troubleshooting does not establish private-plan beam compatibility or
  clinical acceptance. Test intended downstream workflows locally with authorized
  data; no patient dataset is included in this repository.

## License

OpenDICOM De-ID is licensed under [BSD-3-Clause](LICENSE). Kitware
dicom-anonymizer uses BSD-3-Clause, and pydicom uses MIT. Dependency notices are
retained in [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES) and collected from the
exact installed versions during packaging. **About** displays bundled notices.

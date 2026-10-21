# Architecture

The processing engine has no Tk dependency. `__main__.py` starts the desktop
with no arguments and routes command-line arguments to `cli.py`. Both call the
same public API and use the same report model.

## Batch lifecycle

1. **Scan and inspect.** Discover files recursively, read DICOM objects, record
   a neutral per-file identifier, discover identifiers and nested references,
   and capture fingerprints needed for preservation checks. Sensitive source
   records stay in memory.
2. **Construct mappings.** Build one mapping across the complete input batch.
   Patient keys and UID references use consistent replacements. Technical UIDs
   are not treated as patient identifiers.
3. **Transform.** Apply the documented metadata policy and Kitware rules where
   appropriate, recurse through sequences, remove private elements, and write
   safe names. The default source organization replaces each source-directory
   component with a neutral label, preserving the full hierarchy so patients,
   studies, and dose runs remain distinct. Where one source directory directly
   contains several patients, its files get an additional pseudonym folder per
   patient. Explicit flat organization remains available.
4. **Validate.** Re-read output and compare against input snapshots and reference
   graphs. The output cannot be declared technically complete merely because
   individual writes succeeded.
5. **Publish.** Only a batch that passes mandatory checks is published. Failure
   and cooperative cancellation clean up unpublished output. Reports distinguish
   technical results from the still-required privacy review.

Read `core/anonymizer.py`, `core/scanner.py`, `core/uid_mapper.py`,
`core/inspection.py`, and `core/validator.py` for the implementation. The policy
and supported object boundary are described in
[the confidentiality policy](confidentiality.md).

## Public API

```python
from threading import Event
from opendicom_deid.core.anonymizer import process_batch
from opendicom_deid.core.models import Options
from opendicom_deid.core.scanner import scan_directory
from opendicom_deid.core.validator import validate_directory

cancel = Event()
scan = scan_directory("input", recursive=True, cancel=cancel)
options = Options(patient_prefix="SUBJECT", date_mode="remove", organization="source")
report = process_batch("input", "output", options, cancel=cancel)
print(report.summary())
report.save("new-report.json")  # Exclusive create, no source paths or mappings.
independent_report = validate_directory("output")
```

The optional progress callback receives `Progress(phase, completed, total)`.
The cancellation token is a `threading.Event`. Input and output directory paths
must be disjoint and output must be new or empty. `Report.status` is
`COMPLETED`, `FAILED`, or `CANCELLED`; `Report.ok` also requires no `FAIL`
findings. A successful report can contain warnings requiring user review.

Independent output inspection has no original data or secret mapping. It can
check parseability, metadata, duplicate UIDs, and references, but cannot prove
pixel/geometry equivalence or identify which missing references predated a
transformation. Only the batch validator has that comparison context.

## Desktop threading

Tk runs on the main thread. One background worker performs scanning or a batch
operation. A queue transfers progress and results to a Tk `after` callback;
worker threads never update widgets. The controls are disabled while a job is
active. Closing during a job requests cancellation and waits for cleanup before
destroying the window. Exceptions shown to users use fixed text because raw
parser exceptions and filesystem errors can contain identifying information.

Selecting input, editing a path, and toggling recursion do not start a worker.
The Scan input button provides an optional preview. De-identify always performs
its own scan so its mappings and integrity checks use the current input.

## Privacy boundaries

Source paths, original UID mappings, and patient keys are in-memory objects,
not report fields. Reports use fixed finding messages, neutral file labels,
and counts. There is no reversible mapping export, persistent preference store,
network client, telemetry, or remote log transport.

Packaging explicitly excludes pydicom's documentation example loader and test
datasets. Its `pydicom.data` Python module remains available because the pixel
utilities import it. A frozen runtime hook substitutes an empty `pydicom.examples` module
before package import, avoiding example lookup/download behavior without
changing the installed dependency or its DICOM processing implementation.

`--check-gui` is a developer packaging check that creates the real main window,
opens and closes Advanced options, and exits. The native build workflow runs it
inside the packaged executable, followed by processing a synthetic CT/RTSTRUCT
chain through that same executable. Only then are build artifacts uploaded.

No automated check guarantees complete de-identification. Pixel annotations,
recognizable anatomy, and unusual content require local human review.

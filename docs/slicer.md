# Importing into 3D Slicer

Install the **SlicerRT** extension and import the output folder through Slicer's
**DICOM** module. Import indexes the files; loading selects readers to display
them. These are separate operations. See the official
[DICOM import and loading guide](https://slicer.readthedocs.io/en/latest/user_guide/modules/dicom.html).

## Start Slicer with both workarounds

On macOS, double-click `scripts/launch_slicer_macos.command` in the extracted
source package. Keep its neighboring Python scripts together. It starts a new
Slicer session with both workarounds below, using the single Slicer application
in `/Applications`. Save any work in an existing Slicer window and use the new
window for the import. A normal Slicer launch does not enable the reader guard.
If multiple Slicer applications are installed, pass the desired `.app` path as
the launcher's argument, or use the console method below.

In any platform's existing Slicer Python Console, both can be applied together:

```python
import runpy
compat = runpy.run_path("/absolute/path/to/opendicom-deid/scripts/slicer_startup_compat.py")
compat["apply_compatibility"]()
```

The database must already be open. Wait for the compatibility-ready message,
then **Advanced → Examine** again and select the appropriate image/RT readers.
The individual sections below explain the changes, their limits, and how to
undo them. These helpers do not modify the Slicer installation or DICOM files.

## A plan offered as a Scalar Volume

A ViewRay plan can use a private SOP class that the current SlicerRT reader does
not recognize. The Scalar Volume reader may then offer a low-confidence image
interpretation despite the plan having no pixels. Loading that entry produces
geometry warnings and GDCM/DCMTK reader errors. It does not establish that the
image series have lost their geometry.

To prevent that image-reader fallback, open Slicer's **Python Console** and run
the following, replacing the example path with your checked-out project's path:

```python
import runpy
reader_compat = runpy.run_path("/absolute/path/to/opendicom-deid/scripts/slicer_reader_compat.py")
reader_compat["apply_compatibility"]()
```

On Windows, use a raw path string such as
`r"C:\path\to\opendicom-deid\scripts\slicer_reader_compat.py"`.
This changes the registered Scalar Volume plugin for the current Slicer session,
including already-created plugin instances. It excludes only the specifically
recognized ViewRay private plan class. It does not change DICOM files, SOP class
UIDs, database contents, preferences, or other readers. A fixed message explains
the exclusion once per session. It does **not** add private-plan beam support.

Then select **Advanced → Examine** in the DICOM browser to refresh the loadable
list. Load CT/MR series with **Scalar Volume** and RTSTRUCT/RTDOSE with **RT**.
If you do not use the helper, deselect the private RTPLAN's **Scalar Volume**
entry manually. This permits viewing the images, structures and dose, but does
**not** import the private plan's beams. Previously cached loadables containing
the private plan are also blocked before image preparation and loading; examine
again to obtain fresh loadables.

To undo the session workaround:

```python
reader_compat["restore_compatibility"]()
```

Restarting Slicer also restores the original reader. After restarting, apply the
helper again when needed. It does not suppress warnings about empty contours or
missing references.

SlicerRT needs support for that vendor class, or a separately validated
conversion, to read those beams. OpenDICOM preserves the class; it does not
silently relabel a private plan as standard RT Plan Storage. See SlicerRT's
[class recognition code](https://github.com/SlicerRt/SlicerRT/blob/master/DicomRtImportExport/Logic/vtkSlicerDicomRtImportExportModuleLogic.cxx)
and [RT reader](https://github.com/SlicerRt/SlicerRT/blob/master/DicomRtImportExport/Logic/vtkSlicerDicomRtReader.cxx).

## SQL errors: database not open

Some SlicerRT builds read the old `DatabaseDirectory` setting, while newer
Slicer versions use a database-schema-specific setting. The extension can fail
to open its database even while Slicer's main DICOM browser has a working one.
This can produce `QSqlQuery::prepare: database not open` and parameter-count
errors independently of the DICOM files.

Open a working database in **DICOM → DICOM database settings** first. To apply
the separate database workaround, open Slicer's **Python Console** and run the
following, replacing the example path with your checked-out project's path.
Use this in addition to the reader workaround if both error types occur:

```python
import runpy
database_compat = runpy.run_path("/absolute/path/to/opendicom-deid/scripts/slicer_database_compat.py")
database_compat["apply_compatibility"]()
```

On Windows, use a raw path string such as
`r"C:\path\to\opendicom-deid\scripts\slicer_database_compat.py"`.
After the success message, retry examination/loading. The helper checks the
open database and saves the original legacy setting under its own
`OpenDICOM` settings keys before changing it. It does not modify DICOM files,
database contents, or the active database selection. Repeated application
keeps the original backup. If you switch databases, apply it again.

To undo the workaround in the same console session:

```python
database_compat["restore_compatibility"]()
```

After restarting Slicer, run the first two console lines to reload the helper,
then call `restore_compatibility()`. Loading the helper alone changes nothing.
The saved backup also records when the legacy setting was originally absent.
The workaround addresses this setting mismatch, not every possible database
error; it does not add private-plan support.

## Empty contours and anonymized names

An empty ROI entry has a definition but no contour coordinates. SlicerRT can
warn about these entries while loading the other structures. Replacing an ROI
name does not explain an empty contour sequence. OpenDICOM checks preservation
of contour coordinates, point counts and retained RT sequence structure;
compare with the original export to establish whether an empty ROI was already
present. Do not invent contours or delete ROI definitions merely to silence the
warning: other plan objects may reference them.

In particular, an air/water override or box label alone does not define a volume
or its material properties. When the public contour and density-override fields
are absent, the anonymized file cannot establish the missing geometry or
density. Public-plan processing also removes vendor-private fields. Before
independent dose reconstruction, determine from the original export and vendor
documentation whether required treatment, override and optimization information
was exported and retained. A readable dose grid and preserved beam parameters
do not establish that all inputs needed to reproduce the dose are available.

Older OpenDICOM versions replaced ROI labels with `ANONYMIZED`. Reprocess the
**original export** with the updated clinical-text policy to preserve useful
labels. The original labels cannot be recovered from an already scrubbed file.
Preserving labels does not add prescription constraints or other plan content
that the treatment system omitted from its export.

## Other messages

An unresolved-reference warning means a referenced instance is absent from the
selected dataset. Compare the report and source export; do not substitute a
different instance merely because its modality or geometry looks similar.
An absent referenced study object is distinct from an absent referenced image.

Qt's missing-font alias message reports a font fallback and its startup cost;
it is unrelated to DICOM geometry, privacy or plan completeness.

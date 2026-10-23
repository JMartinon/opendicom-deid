# Importing into 3D Slicer

Install the **SlicerRT** extension and import the output folder through Slicer's
**DICOM** module. Import indexes the files; loading selects readers to display
them. These are separate operations. See the official
[DICOM import and loading guide](https://slicer.readthedocs.io/en/latest/user_guide/modules/dicom.html).

## A plan offered as a Scalar Volume

A ViewRay plan can use a private SOP class that the current SlicerRT reader does
not recognize. The Scalar Volume reader may then offer a low-confidence image
interpretation despite the plan having no pixels. Loading that entry produces
geometry warnings and GDCM/DCMTK reader errors. It does not establish that the
image series have lost their geometry.

In the DICOM browser, select **Advanced → Examine**. Load CT/MR series with the
**Scalar Volume** reader and RTSTRUCT/RTDOSE with the **RT** reader. Deselect the
private RTPLAN's **Scalar Volume** entry. This permits viewing the images,
structures and dose, but does **not** import the private plan's beams.

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
the compatibility workaround, open Slicer's **Python Console** and run the
following, replacing the example path with your checked-out project's path:

```python
import runpy
compat = runpy.run_path("/absolute/path/to/opendicom-deid/scripts/slicer_database_compat.py")
compat["apply_compatibility"]()
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
compat["restore_compatibility"]()
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
of contour coordinates and point counts; compare with the original export to
establish whether an empty ROI was already present. Do not invent contours or
delete ROI definitions merely to silence the warning: other plan objects may
reference them.

Older OpenDICOM versions replaced ROI labels with `ANONYMIZED`. Reprocess the
**original export** with the updated clinical-text policy to preserve useful
labels. The original labels cannot be recovered from an already scrubbed file.
Preserving labels does not add prescription constraints or other plan content
that the treatment system omitted from its export.

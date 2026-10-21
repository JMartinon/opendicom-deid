# Third-party notices

This directory retains source-distribution attribution notices. The build
process additionally copies the exact installed distribution license files for
runtime dependencies and the Python and PyInstaller licenses into the artifact.
The application's About window displays those bundled notices.

| Component | License / source |
| --- | --- |
| Kitware dicom-anonymizer | BSD-3-Clause; license text retained in this directory |
| pydicom | MIT; copied from the installed distribution |
| tqdm | MPL-2.0 and MIT notices; copied from the installed distribution |
| Python | PSF and historical licenses; copied from the installed interpreter |
| Tcl | Tcl permissive license; `Tcl.txt` |
| Tk | Tk permissive license; `Tk.txt` |
| PyInstaller bootloader | GPL with the distributed-app exception; copied from the installed distribution |

Tcl and Tk notices were retrieved from the upstream
[Tcl](https://github.com/tcltk/tcl/blob/core-8-6-branch/license.terms) and
[Tk](https://github.com/tcltk/tk/blob/core-8-6-branch/license.terms) repositories.
Redistributors must review native-library notices when changing the build
runtime, target platform, or dependencies. Project licensing does not override
third-party licensing terms.

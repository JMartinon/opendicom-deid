# Native distribution and verification

Build with Python 3.12 or newer and a working Tcl/Tk installation. PyInstaller
must run on the target operating system; it is not a cross-compiler. Python and
Tcl/Tk are included in the resulting desktop artifact. See the
[PyInstaller usage documentation](https://pyinstaller.org/en/stable/usage.html).

## Desktop packages

| Target | CI runner | Package |
| --- | --- | --- |
| Windows x86-64 | `windows-2022` | ZIP containing a standalone `.exe` and external notices |
| macOS Apple Silicon | `macos-15` | ZIP containing an `.app`, with notices inside the bundle |
| macOS Intel | `macos-15-intel` | Separate Intel `.app` ZIP |
| Ubuntu x86-64 | `ubuntu-24.04` | Portable directory in `.tar.gz` |

Runner labels are documented by
[GitHub's runner-images project](https://github.com/actions/runner-images).
The workflows use explicit native architecture settings and commit-pinned
official GitHub Actions. They do not upload source DICOM files or input folders.

## Open an application

**Windows:** extract the ZIP and double-click `OpenDICOM-De-ID.exe`. It extracts
its runtime to the operating system's temporary location. This contains program
files, not copies of input DICOM files. The executable is unsigned. Windows may
display an unknown-publisher or SmartScreen warning; follow your organization's
policy and verify the build source/checksum before permitting it.

**macOS:** extract the archive, then move `OpenDICOM-De-ID.app` to Applications
if desired. Use the build matching your CPU architecture. Builds may receive
PyInstaller ad-hoc signing, which is not a trusted publisher signature. They
are not notarized. macOS may block first launch. If permitted by your local
policy, use the macOS **Privacy & Security** settings to allow the verified app.
Do not disable Gatekeeper globally. Minimum OS compatibility depends on the
Python/Tcl/Tk runtime used to build the artifact; the CI targets macOS 15.

**Ubuntu:** extract the entire archive and keep its files together. Open the
`OpenDICOM-De-ID` executable from your file manager. The supplied `.desktop`
launcher may require **Allow Launching** or marking the file as trusted. The CI
build targets Ubuntu 24.04 or newer (glibc 2.39); X11/XWayland and the usual
desktop libraries must be available. No AppImage or system installer is
provided. A build created on a newer distribution cannot be assumed to run on
an older one. See [PyInstaller Linux portability](https://pyinstaller.org/en/stable/usage.html#making-linux-apps-forward-compatible).

## Build and release

From an activated virtual environment, run the matching wrapper:

```sh
bash scripts/build_macos.sh
bash scripts/build_linux.sh
```

```powershell
.\scripts\build_windows.ps1
```

Each wrapper installs `.[build,dev]`, runs pytest, and invokes
`scripts/build.py`. A missing Tk installation or missing dependency license
causes the build to fail. `dist/release/` receives the archive and SHA-256 file.
Run `python scripts/smoke_gui.py` in a desktop session for a quick window test;
Linux CI uses `xvfb-run -a`.

The packaging process copies the exact installed runtime dependency licenses
and a version manifest, Python's license, the PyInstaller bootloader exception,
and the checked-in Tcl/Tk notices. Audit additional native-library notices when
changing Python distributions or adding dependencies. Build environments should
be clean, with no patient files or optional clinical data packages installed.

GitHub Actions test and build jobs contain synthetic tests only. Tagging a
matching package version (for example `v0.1.0`) produces a **draft prerelease**.
Review artifacts before publishing the draft. Signing and notarization require
separately configured credentials and are not part of these workflows.

## Acceptance checks before distribution

1. Run the full synthetic test suite on the target platform.
2. Launch the extracted artifact on a clean computer without Python.
3. Scan and process the synthetic RT reference chain; read every validation
   result and confirm output counts, reference integrity, and unchanged pixels.
4. Verify cancellation, invalid output directories, report export, and About
   license viewing. Reports must use a new filename outside input.
5. Import a synthetic output into the intended independent viewer, including
   Slicer/SlicerRT if it is part of the intended workflow.
6. Validate representative authorized datasets locally under an appropriate
   review process. Never add them to the repository or release artifacts.

A successful build is not a privacy or clinical validation. Windows, Intel
macOS, and Ubuntu native artifacts cannot be exercised from an Apple Silicon
macOS development host; their actual CI and clean-machine results are required.

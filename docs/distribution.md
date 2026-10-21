# Native distribution and verification

Build with Python 3.12 or newer and a working Tcl/Tk installation. PyInstaller
must run on the target operating system; it is not a cross-compiler. Python and
Tcl/Tk are included in the resulting desktop artifact. See the
[PyInstaller usage documentation](https://pyinstaller.org/en/stable/usage.html).

## Desktop packages

End users can run the packaged application without installing Python or using
build commands. Download a platform archive from a published version's
[release assets](https://github.com/JMartinon/opendicom-deid/releases), when
available, or from a successful
[Native desktop builds run](https://github.com/JMartinon/opendicom-deid/actions/workflows/build.yml).

For Windows Actions downloads, open the run's **Artifacts** section and choose
**Windows-application**. Extract its ZIP once; `OpenDICOM-De-ID.exe` is directly
inside, alongside `licenses/` and this guide. Double-click the executable. No
Python installation or PowerShell commands are needed.

The artifacts named **OpenDICOM-De-ID-…** contain versioned distribution archives
and SHA-256 files. GitHub wraps those in an outer ZIP: extract that ZIP, then
extract the application archive inside it. This also applies to the older
Windows builds, which only offered **OpenDICOM-De-ID-Windows-x86_64**.

This repository is private. Sign in to GitHub with an account that has
repository access to download its builds. Actions artifacts expire after **14
days** under this project's retention setting. Published release assets do not
use that retention period; they remain available until removed. Draft releases
are for maintainer review and must be published before ordinary repository
readers can download them. See
[GitHub's artifact download instructions](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).

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

**Ubuntu:** extract the entire archive and keep its files together, including
the `_internal/` directory containing the runtime. Open the `OpenDICOM-De-ID`
executable inside the extracted folder from your file manager. The supplied `.desktop`
launcher may require **Allow Launching** or marking the file as trusted. The CI
build targets Ubuntu 24.04 or newer (glibc 2.39); X11/XWayland and the usual
desktop libraries must be available. No AppImage or system installer is
provided. A build created on a newer distribution cannot be assumed to run on
an older one. See [PyInstaller Linux portability](https://pyinstaller.org/en/stable/usage.html#making-linux-apps-forward-compatible).

## Build and release

Build commands are for maintainers and developers. For macOS and Windows, run
the matching wrapper from an activated virtual environment with Tcl/Tk:

```sh
bash scripts/build_macos.sh
```

```powershell
.\scripts\build_windows.ps1
```

The wrappers install `.[build,dev]`, run pytest, and invoke `scripts/build.py`.
A missing dependency license causes the build to fail. `dist/release/` receives
the archive and SHA-256 file. Linux interpreter selection and Tk checks are
described below.

### Launch after a local build

The completed application is in `dist/`; `build/` holds intermediate files used
by the packaging tools. The build script prints the exact application path and
launch command at completion.

On Linux, run this from the repository root:

```sh
./dist/OpenDICOM-De-ID/OpenDICOM-De-ID
```

Keep the executable together with `dist/OpenDICOM-De-ID/_internal/` and the
other files in that folder. Launching the intermediate executable under
`build/pyinstaller/` causes a missing Python shared-library error because that
directory does not contain the bundled runtime.

On Windows, double-click `dist/OpenDICOM-De-ID.exe`; on macOS, open
`dist/OpenDICOM-De-ID.app`. To distribute an application, share its archive from
`dist/release/` and extract the complete archive on the destination computer.

### Linux build setup

On Ubuntu 24.04 or newer, install Tcl/Tk support, the virtual environment module,
and the shared Python library needed for packaging, then select the system interpreter:

```sh
sudo apt-get update
sudo apt-get install python3-tk python3-venv libpython3-dev
PYTHON=/usr/bin/python3 bash scripts/build_linux.sh
```

The Linux wrapper checks Python 3.12+ and Tcl/Tk before dependency installation
or tests. With no `PYTHON` override, it first checks `python3` on your path
(including an active virtual environment), then tries `/usr/bin/python3` if
necessary. It prints the selected interpreter. An explicit `PYTHON` selection
does not fall back to another interpreter if its check fails. A compatible
selected virtual environment is reused; otherwise the wrapper creates an
isolated `.venv-build-linux` environment rather than installing into system
Python. It does not install OS packages automatically.

Packaging prioritizes the selected Python's shared library over libraries from
other Python installations on `LD_LIBRARY_PATH`. This prevents mixing system
Python modules with an incompatible runtime from a toolchain or Conda environment.

`python3-tk` matches your distribution's default Python. For example, installing
it for Ubuntu's Python 3.12 does not supply `_tkinter` to a separately installed
Python 3.14 or a virtual environment created from that Python. Use the system
interpreter as above, install the matching Tk support from your custom Python
provider, or rebuild that Python with Tcl/Tk support. Installing a package named
`tkinter` with pip does not repair a missing standard-library `_tkinter` module.
See [Python's optional Tkinter module documentation](https://docs.python.org/3/library/tkinter.html).

Packaging and the Tk prerequisite check can run without a display. Opening the
GUI or running a GUI smoke test requires a desktop session or a virtual display.
Use the selected build environment's Python for `scripts/smoke_gui.py`; on a
headless Ubuntu host, install `xvfb` and use, for example:

```sh
xvfb-run -a .venv-build-linux/bin/python scripts/smoke_gui.py
xvfb-run -a .venv-build-linux/bin/python scripts/smoke_frozen.py
```

If the wrapper reused an existing virtual environment, use its Python instead.

### Release preparation

The packaging process copies the exact installed runtime dependency licenses
and a version manifest, Python's license, the PyInstaller bootloader exception,
and the checked-in Tcl/Tk notices. Audit additional native-library notices when
changing Python distributions or adding dependencies. Build environments should
be clean, with no patient files or optional clinical data packages installed.

GitHub Actions test and build jobs contain synthetic tests only. Tagging a
matching package version (for example `v0.1.4`) produces a **draft prerelease**.
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

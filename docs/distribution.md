# Native distribution and verification

**Under active development; not intended for clinical use.** Packages are for
research and development evaluation. Do not use them for diagnosis, treatment
planning, treatment delivery or clinical decision-making. A successful build
does not establish complete de-identification or preservation of every
proprietary treatment field. Keep this notice and the license notices with
redistributed packages.

Build with Python 3.12 or newer and a working Tcl/Tk installation. PyInstaller
must run on the target operating system; it is not a cross-compiler. Python and
Tcl/Tk are included in the resulting desktop artifact. See the
[PyInstaller usage documentation](https://pyinstaller.org/en/stable/usage.html).

## Desktop packages

Windows development releases provide a direct `.exe` download as well as a ZIP
with the executable, external license notices and this guide. Both contain the
same verified application. End users can double-click the `.exe` without a
Python installation; the packaged About view includes license notices too.

End users can run the packaged application without installing Python or using
build commands. Use an archive built locally by the maintainer or a published
version's [release assets](https://github.com/JMartinon/opendicom-deid/releases),
when available. Existing or explicitly requested hosted packages are also
available from a successful
[Native desktop builds run](https://github.com/JMartinon/opendicom-deid/actions/workflows/build.yml).

For Windows Actions downloads, open the run's **Artifacts** section and choose
**Windows-application**. Extract its ZIP once; `OpenDICOM-De-ID.exe` is directly
inside, alongside `licenses/` and this guide. Double-click the executable. No
Python installation or PowerShell commands are needed.

The artifacts named **OpenDICOM-De-ID-…** contain versioned distribution archives
and SHA-256 files. GitHub wraps those in an outer ZIP: extract that ZIP, then
extract the application archive inside it. This also applies to the older
Windows builds, which only offered **OpenDICOM-De-ID-Windows-x86_64**.

The repository and published release assets are public. Actions artifact
downloads require signing in to GitHub and expire after **14 days** under this
project's retention setting. Published release assets do not use that retention
period; they remain available until removed. Draft releases are for maintainer
review and must be published before other readers can download them. Check the
version of historical hosted packages before sharing them. See
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

The local Linux workstation uses **Ubuntu 26.04, x86-64, glibc 2.43**. Its
packages target that system; do not assume they work on Ubuntu 24.04. Build on
the oldest supported Linux system when an older distribution is required.

## Build and release

**Build locally by default.** Pushes, pull requests and version tags do not start
GitHub Actions. All hosted workflows require an explicit manual run; the full
test matrix and native packages still consume Actions minutes when requested.
Do not dispatch them merely to make a local build.

Version 0.3.0 adds optional parallel file processing and retains automatic OS
appearance and consistent scrollbars. It uses the existing runtime with no
additional Python dependency or network service. Build and check the
macOS package on the Mac, the Linux package on the workstation, and the Windows
executable on a Windows computer using the commands below. Hosted builds are
optional and are not needed for this update.

Build commands are for maintainers and developers. A native build needs the
project source and an internet connection for Python build dependencies; it
does not need patient data. Transfer only source and packaged applications
between build computers. The finished application continues to work offline.

### Windows: build on a Windows computer

1. Install 64-bit Python 3.12 or newer from [python.org](https://www.python.org/downloads/windows/),
   including **Tcl/Tk** and the **Python launcher**.
2. Clone the repository, or download and extract its source ZIP. Keep the whole
   source folder together, including `src`, `tests`, `scripts`, and the licenses.
3. Double-click **`scripts\build_windows.cmd`**. The window remains open so you
   can read the result. A desktop session is required for the GUI checks.
4. After **Verified Windows application** appears, share the versioned ZIP in
   **`dist\release`**. Recipients extract it and double-click the `.exe`; only
   the build computer needs Python.

The launcher checks Python/Tcl first, creates its own `.venv-build-windows`,
installs dependencies there, and runs lint, the synthetic tests, GUI checks,
packaging, and checks of the packaged executable. It prefers Python 3.12 when
creating the environment and reuses a compatible existing build environment.
An incompatible environment is left intact; rename it before retrying.

If a Windows build fails once during synthetic processing but succeeds on a
complete rerun, the failure was intermittent. The pip upgrade notice is not a
build error. Do not bypass failed checks; use the executable only after the
launcher reports **Verified Windows application**.

Since 0.2.1, the engine briefly retries Windows sharing/lock conflicts when
opening a new output file, publishing the validated directory, or cleaning up
incomplete output. Publication and cleanup also retry Windows access-denied
errors, which can result from open handles. Retries do not change permissions
or replay partial DICOM writes. Persistent errors still fail. Reports identify
the processing stage and numeric OS error codes, without paths or raw exception
messages. These details help distinguish an access conflict from a DICOM problem;
an earlier generic error alone cannot establish the cause.

From PowerShell, the same build can be started with:

```powershell
.\scripts\build_windows.cmd
```

To select a specific Python installation, pass its executable path:

```powershell
.\scripts\build_windows.cmd -Python "C:\Path\To\Python312\python.exe"
```

The CMD launcher applies its script execution setting to that PowerShell
process only. It does not change the computer's saved execution policy.

### macOS: build on the Mac

Create or activate a virtual environment using a Python with working Tcl/Tk,
then run the wrapper and local checks:

```sh
python3 -m venv .venv-build
source .venv-build/bin/activate
bash scripts/build_macos.sh
python -m ruff check src tests scripts
python scripts/smoke_gui.py
python scripts/smoke_frozen.py
```

The macOS and Linux wrappers install `.[build,dev]`, run pytest, and invoke `scripts/build.py`.
A missing dependency license causes the build to fail. `dist/release/` receives
the archive and SHA-256 file. Linux interpreter selection and Tk checks are
described below. Build each macOS architecture on a matching host/interpreter;
an Apple Silicon build does not also create an Intel build.

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
.venv-build-linux/bin/python -m ruff check src tests scripts
xvfb-run -a .venv-build-linux/bin/python scripts/smoke_gui.py
xvfb-run -a .venv-build-linux/bin/python scripts/smoke_frozen.py
```

If the wrapper reused an existing virtual environment, use its Python instead.
An SSH build on a Linux workstation uses the same commands in its source
checkout. Retrieve the resulting `dist/release/` archive and checksum; keep
clinical files out of the source checkout and build transfer.

### Release preparation

The packaging process copies the exact installed runtime dependency licenses
and a version manifest, Python's license, the PyInstaller bootloader exception,
and the checked-in Tcl/Tk notices. Audit additional native-library notices when
changing Python distributions or adding dependencies. Build environments should
be clean, with no patient files or optional clinical data packages installed.

GitHub Actions test and build jobs contain synthetic tests only. Creating a
version tag does **not** build or release anything. To spend hosted minutes on
a draft prerelease deliberately, start **Draft versioned release** manually
against a matching version tag; that workflow builds all four native packages.
Alternatively, upload locally built archives and checksums when preparing a
release. Review artifacts before publishing. Signing and notarization require
separately configured credentials and are not part of these workflows.

When only Windows is needed, **Verified Windows executable** is a separate
manual workflow using one Windows runner. Supply `release_version` and the full
40-character `expected_commit` for the selected branch or tag. The workflow
checks both before installing dependencies, runs all tests and source/frozen
GUI checks (including parallel RT processing), and confirms the archive contains
the tested executable. Its artifact includes SHA-256 and provenance records.
After verifying the artifact against the successful run and exact source commit,
publish its executable and archive with matching checksums as a development
prerelease. Never promote an incomplete or failed build.

## Acceptance checks before distribution

1. Run the full synthetic test suite on the target platform.
2. Launch the extracted artifact on a clean computer without Python.
3. Scan and process the synthetic RT reference chain; read every validation
   result and confirm output counts, reference integrity, and unchanged pixels.
   Also exercise the synthetic ViewRay reference-class
   correction with the research option enabled and check its explicit warning.
4. Verify cancellation, invalid output directories, report export, and About
   license viewing. Reports must use a new filename outside input.
5. Change the **operating system's** application appearance, including during synthetic
   processing. Check fields, findings, Advanced options and inspection/About
   dialogs for readable text. Restart to verify the current OS setting is used;
   no theme button or app-specific preference should be present. Verify the
   scrollbar is hidden for short content and can drag/page through long content.
   Watch the visible bar and completed-file count through each processing stage;
   stage resets and animated unknown-length steps must not imply overall success.
6. Process a synthetic batch with **Parallel files** set to 1 and then 2 or 4.
   Verify the same output counts and validation results, responsive progress,
   cancellation and cleanup. Workers apply only to file transformation; scanning
   and validation remain sequential. Each worker needs additional memory.
7. Import a synthetic output into the intended independent viewer, including
   Slicer/SlicerRT if it is part of the intended workflow.
8. Validate representative authorized datasets locally under an appropriate
   review process. Never add them to the repository or release artifacts.

A successful build is not a privacy or clinical validation. Windows, Intel
macOS, and Ubuntu native artifacts cannot be exercised from an Apple Silicon
macOS development host; validate them on the corresponding operating system,
using local computers or a deliberately requested CI run.

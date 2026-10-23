param([string]$Python = "")
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Run this script on Windows; use the native build script for your operating system."
}

function Test-BuildPython {
    param([string]$Executable, [string[]]$Arguments = @())
    try {
        $json = & $Executable @Arguments scripts/build_environment.py --json 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $result = $json | ConvertFrom-Json
        if ($result.error) { return $null }
        return $result
    } catch { return $null }
}

Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $environmentDirectory = Join-Path (Get-Location) ".venv-build-windows"
    $buildPython = Join-Path $environmentDirectory "Scripts\python.exe"
    $selected = $null
    if ($Python) {
        # An explicit interpreter choice must not silently fall back.
        $selected = Test-BuildPython $Python
    } elseif (Test-Path $environmentDirectory) {
        $selected = Test-BuildPython $buildPython
    } else {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $selected = Test-BuildPython "py" @("-3.12")
            if (-not $selected) { $selected = Test-BuildPython "py" @("-3") }
        }
        if (-not $selected -and (Get-Command python -ErrorAction SilentlyContinue)) {
            $selected = Test-BuildPython "python"
        }
    }
    if (-not $selected) {
        throw @"
No usable Python 3.12+ with Tcl/Tk was found.
Install Python from python.org with Tcl/Tk and the Python launcher enabled.
If .venv-build-windows is incomplete or its Python was removed, rename that
folder and retry. You can select Python explicitly with -Python C:\path\python.exe.
"@
    }

    if (Test-Path $environmentDirectory) {
        $existing = Test-BuildPython $buildPython
        if (-not $existing -or -not $existing.is_venv -or
            $existing.base_executable -ne $selected.base_executable -or
            $existing.version -ne $selected.version) {
            throw "Existing .venv-build-windows is incompatible. Rename it and retry; it has been left unchanged."
        }
    } else {
        Write-Host "Creating isolated build environment: $environmentDirectory"
        & $selected.executable -m venv $environmentDirectory
        if ($LASTEXITCODE -ne 0) { throw "Could not create the build virtual environment" }
    }
    & $buildPython scripts/build_environment.py
    if ($LASTEXITCODE -ne 0) { throw "Build interpreter is missing required GUI support" }
    & $buildPython -m pip install ".[build,dev]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    & $buildPython -m ruff check src tests scripts
    if ($LASTEXITCODE -ne 0) { throw "Lint checks failed" }
    & $buildPython -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
    & $buildPython scripts/smoke_gui.py
    if ($LASTEXITCODE -ne 0) { throw "GUI checks failed" }
    & $buildPython scripts/build.py
    if ($LASTEXITCODE -ne 0) { throw "Packaging failed" }
    # Running a windowed executable through Python waits for its exit status.
    & $buildPython scripts/smoke_frozen.py
    if ($LASTEXITCODE -ne 0) { throw "Packaged application checks failed" }
    Write-Host ""
    Write-Host "Verified Windows application: $(Join-Path (Get-Location) 'dist\OpenDICOM-De-ID.exe')"
    Write-Host "Share the complete versioned ZIP in dist\release."
} finally { Pop-Location }

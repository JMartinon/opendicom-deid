param([string]$Python = "python")
$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & $Python -m pip install ".[build,dev]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    & $Python -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
    & $Python scripts/build.py
    if ($LASTEXITCODE -ne 0) { throw "Packaging failed" }
} finally { Pop-Location }
